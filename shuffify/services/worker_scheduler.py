"""Worker-only in-memory scheduling with fenced leadership and exact due identities."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.executors.base import BaseExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_RUNNING
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, select
from sqlalchemy.exc import DBAPIError, DisconnectionError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from shuffify.models.db import Schedule
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.schedule_utils import schedule_revision
from shuffify.services.scheduled_admission import AdmissionResult, admit_scheduled, build_cadence, utc

logger = logging.getLogger(__name__)
LOCK_ID = 88442211
GRACE_SECONDS = 3600


def now_utc():
    return datetime.now(timezone.utc)


class DurableAdmissionExecutor(BaseExecutor):
    """APScheduler 3.x extension: pass exact run_times to local queue admission."""

    def __init__(self, *, clock=now_utc, max_workers=4):
        super().__init__()
        self.clock = clock
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="schedule-admission")

    def _run_due(self, job, run_times):
        events = []
        times = run_times[-1:] if job.coalesce else run_times
        for run_time in times:
            due = utc(run_time)
            if job.misfire_grace_time is not None and utc(self.clock()) - due > timedelta(
                seconds=job.misfire_grace_time
            ):
                events.append(JobExecutionEvent(EVENT_JOB_MISSED, job.id, job._jobstore_alias, due))
                continue
            try:
                result = job.func(*job.args, scheduled_for=due, **job.kwargs)
            except Exception as error:
                logger.warning("Scheduled admission failed (%s)", type(error).__name__)
                events.append(
                    JobExecutionEvent(
                        EVENT_JOB_ERROR,
                        job.id,
                        job._jobstore_alias,
                        due,
                        exception=RuntimeError("Scheduled admission failed"),
                    )
                )
            else:
                events.append(JobExecutionEvent(EVENT_JOB_EXECUTED, job.id, job._jobstore_alias, due, retval=result))
        return events

    def _do_submit_job(self, job, run_times):
        future = self._pool.submit(self._run_due, job, run_times)

        def complete(result):
            if result.exception() is not None:
                self._run_job_error(job.id, result.exception())
            else:
                self._run_job_success(job.id, result.result())

        future.add_done_callback(complete)

    def shutdown(self, wait=True):
        self._pool.shutdown(wait=wait)


def _trigger_identity(trigger):
    if not isinstance(trigger, (IntervalTrigger, CronTrigger)):
        return None
    fields = {
        "timezone": str(trigger.timezone),
        "start": str(trigger.start_date),
        "end": str(trigger.end_date),
        "jitter": trigger.jitter,
    }
    if isinstance(trigger, IntervalTrigger):
        return {**fields, "interval": trigger.interval.total_seconds()}
    return {**fields, "fields": [str(field) for field in trigger.fields]}


class WorkerScheduler:
    """Refresh on explicit events; healthy idle work never scans schedule tables."""

    def __init__(
        self,
        app,
        repository=None,
        *,
        clock=now_utc,
        scheduler_factory=BackgroundScheduler,
        connection_factory=None,
        wake_callback=None,
        event_callback=None,
        admission_callback=admit_scheduled,
        leader_check_interval=5,
        retry_initial=0.1,
        retry_max=5,
        connect_timeout=3,
    ):
        self.app = app
        with app.app_context():
            self.repository = repository or OperationRepository()
        self.clock, self._scheduler_factory = clock, scheduler_factory
        self.timezone = str(ZoneInfo(app.config.get("SCHEDULER_TIMEZONE", "UTC")))
        self._wake_callback, self._event_callback, self._admit = wake_callback, event_callback, admission_callback
        self._enabled = app.config.get("SCHEDULER_ENABLED", True)
        self._requires_lock = self.repository.engine.dialect.name == "postgresql" or connection_factory is not None
        self._connect_timeout = connect_timeout
        self._connect, self._engine = connection_factory, None
        if self._requires_lock and connection_factory is None:
            self._engine = create_engine(
                self.repository.engine.url,
                poolclass=NullPool,
                connect_args={
                    "connect_timeout": connect_timeout,
                    "keepalives": 1,
                    "keepalives_idle": 10,
                    "keepalives_interval": 5,
                    "keepalives_count": 3,
                },
            )
            self._connect = self._engine.raw_connection
        self._retry_initial, self._retry_max = max(0.001, retry_initial), max(0.001, retry_initial, retry_max)
        self._leader_interval = max(0.001, leader_check_interval)
        self._state_lock, self._connection_lock = threading.RLock(), threading.RLock()
        self._stop, self._event, self.ready = threading.Event(), threading.Event(), threading.Event()
        self._thread, self._scheduler, self._connection = None, None, None
        self._closed, self._leader = False, False
        self._worker_id, self._generation = uuid4().hex, None
        self._requested, self._applied = 1, 0
        self._installed, self._metrics = {}, {}

    def __enter__(self):
        return self.start()

    def __exit__(self, *error):
        self.close()

    def start(self):
        with self._state_lock:
            if self._closed:
                raise RuntimeError("A closed scheduler cannot restart")
            if self._enabled and self._thread is None:
                self._thread = threading.Thread(target=self._run, name="worker-scheduler", daemon=True)
                self._thread.start()
        return self

    def _emit(self, name, value=None):
        with self._state_lock:
            self._metrics[name] = self._metrics.get(name, 0) + 1
        if self._event_callback:
            try:
                self._event_callback(name, value)
            except Exception as error:
                logger.warning("Scheduler event callback failed (%s)", type(error).__name__)

    @property
    def metrics(self):
        with self._state_lock:
            return dict(self._metrics)

    def request_refresh(self):
        with self._state_lock:
            self._requested += 1
        self._event.set()

    @staticmethod
    def _driver(connection):
        return getattr(connection, "driver_connection", connection)

    @staticmethod
    def _close_connection(connection):
        if connection is not None:
            try:
                if hasattr(connection, "invalidate"):
                    connection.invalidate()
                connection.close()
            except Exception as error:
                logger.warning("Scheduler lock connection close failed (%s)", type(error).__name__)

    def _acquire(self):
        connection = None
        try:
            if self._requires_lock:
                connection = self._connect()
                driver = self._driver(connection)
                driver.autocommit = True
                cursor = driver.cursor()
                try:
                    cursor.execute(f"SELECT pg_try_advisory_lock({LOCK_ID})")
                    if not cursor.fetchone()[0]:
                        self._close_connection(connection)
                        return False
                finally:
                    cursor.close()
            with self._connection_lock:
                self._connection = connection
            with self._state_lock:
                if self._stop.is_set():
                    self._close_connection(connection)
                    self._connection = None
                    return False
                self._leader, self._generation = True, uuid4().hex
                self._requested += 1
            self._emit("leader_acquisitions")
            return True
        except Exception as error:
            self._close_connection(connection)
            logger.warning("Scheduler leadership unavailable (%s)", type(error).__name__)
            self._emit("leader_retries")
            return False

    def _probe(self, generation=None):
        with self._state_lock:
            if self._stop.is_set() or not self._leader or generation is not None and generation != self._generation:
                return False
        try:
            if self._requires_lock:
                with self._connection_lock:
                    if self._connection is None:
                        return False
                    cursor = self._driver(self._connection).cursor()
                    try:
                        cursor.execute("SELECT 1")
                        cursor.fetchone()
                    finally:
                        cursor.close()
            with self._state_lock:
                return (
                    self._leader and not self._stop.is_set() and (generation is None or generation == self._generation)
                )
        except Exception as error:
            logger.warning("Scheduler leadership lost (%s)", type(error).__name__)
            self._lose_leadership()
            return False

    def _lose_leadership(self):
        with self._state_lock:
            was_leader = self._leader
            self._leader, self._generation = False, None
            self.ready.clear()
            self._installed = {}
            if self._scheduler is not None:
                self._scheduler.pause()
                self._scheduler.remove_all_jobs()
        with self._connection_lock:
            connection, self._connection = self._connection, None
        self._close_connection(connection)
        if was_leader:
            self._emit("leadership_losses")
        self._event.set()

    def _ensure_scheduler(self):
        if self._scheduler is None:
            scheduler = self._scheduler_factory(
                timezone=self.timezone,
                jobstores={"default": MemoryJobStore()},
                executors={
                    "default": DurableAdmissionExecutor(
                        clock=self.clock, max_workers=self.app.config.get("SCHEDULER_THREAD_POOL_SIZE", 4)
                    )
                },
                job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": GRACE_SECONDS},
            )
            scheduler.start(paused=True)
            scheduler.add_listener(lambda event: self._emit("missed"), EVENT_JOB_MISSED)
            scheduler.add_listener(lambda event: self._emit("admission_errors"), EVENT_JOB_ERROR)
            self._scheduler = scheduler

    def _refresh(self):
        with self._state_lock:
            request, generation = self._requested, self._generation
        specs = {}
        with Session(self.repository.engine) as storage:
            for schedule in storage.scalars(select(Schedule).where(Schedule.is_enabled.is_(True))):
                try:
                    specs[schedule.id] = (schedule_revision(schedule), build_cadence(schedule, self.timezone))
                except (ValueError, TypeError, ZoneInfoNotFoundError):
                    self._emit("invalid_schedules")
        if not self._probe(generation):
            return False
        self._ensure_scheduler()
        with self._state_lock:
            if generation != self._generation or self._stop.is_set():
                return False
            expected_ids = {f"schedule_{schedule_id}" for schedule_id in specs}
            for job in self._scheduler.get_jobs():
                if job.id.startswith("schedule_") and job.id not in expected_ids:
                    self._scheduler.remove_job(job.id)
            installed = {}
            for schedule_id, (revision, cadence) in specs.items():
                job_id = f"schedule_{schedule_id}"
                job = self._scheduler.get_job(job_id)
                args = (schedule_id, cadence.fingerprint, generation)
                if (
                    job is None
                    or tuple(job.args) != args
                    or job.next_run_time is None
                    or _trigger_identity(job.trigger) != _trigger_identity(cadence.trigger)
                ):
                    next_fire = cadence.trigger.get_next_fire_time(None, utc(self.clock()) + timedelta(microseconds=1))
                    self._scheduler.add_job(
                        self._fire,
                        trigger=cadence.trigger,
                        id=job_id,
                        args=args,
                        replace_existing=True,
                        next_run_time=next_fire,
                    )
                installed[schedule_id] = {
                    "installed_revision": revision,
                    "cadence_fingerprint": cadence.fingerprint,
                    "trigger_identity": _trigger_identity(cadence.trigger),
                }
            self._installed, self._applied = installed, request
            self._scheduler.resume()
            self.ready.set()
        self._emit("refreshes")
        return True

    def _run(self):
        delay, refresh_retry = self._retry_initial, None
        next_probe = time.monotonic()
        try:
            while not self._stop.is_set():
                self._event.clear()
                if not self._leader:
                    if not self._acquire():
                        self._event.wait(delay)
                        delay = min(self._retry_max, delay * 2)
                        continue
                    delay, refresh_retry = self._retry_initial, None
                    next_probe = time.monotonic() + self._leader_interval
                if self._requires_lock and time.monotonic() >= next_probe:
                    if not self._probe():
                        continue
                    next_probe = time.monotonic() + self._leader_interval
                with self._state_lock:
                    refresh_needed = self._requested > self._applied
                if refresh_needed and (refresh_retry is None or time.monotonic() >= refresh_retry):
                    try:
                        if not self._refresh():
                            continue
                        delay, refresh_retry = self._retry_initial, None
                    except Exception as error:
                        logger.warning("Scheduler refresh unavailable (%s)", type(error).__name__)
                        self._emit("refresh_failures")
                        refresh_retry = time.monotonic() + delay
                        delay = min(self._retry_max, delay * 2)
                waits = []
                if self._requires_lock:
                    waits.append(max(0, next_probe - time.monotonic()))
                if refresh_retry is not None:
                    waits.append(max(0, refresh_retry - time.monotonic()))
                self._event.wait(min(waits) if waits else None)
        finally:
            self._lose_leadership()
            if self._scheduler is not None:
                self._scheduler.shutdown(wait=True)

    def _fire(self, schedule_id, expected_cadence, generation, *, scheduled_for=None):
        due, delay = utc(scheduled_for), self._retry_initial
        while True:
            if not self._probe(generation):
                return AdmissionResult("skipped", due, code="leadership_lost")
            if utc(self.clock()) - due > timedelta(seconds=GRACE_SECONDS):
                self._emit("missed")
                return AdmissionResult("skipped", due, code="misfire")
            try:
                result = self._admit(
                    self.repository,
                    schedule_id,
                    expected_cadence,
                    due,
                    default_timezone=self.timezone,
                    is_leader=lambda: self._probe(generation),
                )
            except (DBAPIError, DisconnectionError) as error:
                if isinstance(error, DBAPIError) and not (
                    isinstance(error, OperationalError) or error.connection_invalidated
                ):
                    raise
                self._emit("admission_retries")
                if self._stop.wait(delay):
                    return AdmissionResult("skipped", due, code="leadership_lost")
                delay = min(self._retry_max, delay * 2)
                continue
            self._emit("admitted" if result.state == "admitted" else "skipped_" + result.code, result.to_dict())
            if result.state == "admitted" and self._wake_callback:
                try:
                    self._wake_callback()
                except Exception as error:
                    logger.warning("Schedule queue wake failed (%s)", type(error).__name__)
                    self._emit("wake_failures")
            if result.code in {"cadence_changed", "schedule_missing", "schedule_disabled"}:
                self.request_refresh()
            return result

    def registration_snapshot(self, schedule_ids):
        ids = list(schedule_ids)
        if len(ids) > 5000 or any(type(value) is not int or value < 1 for value in ids):
            raise ValueError("Registration reads require bounded positive schedule IDs")
        with self._state_lock:
            running = (
                self._leader
                and self._scheduler is not None
                and self._scheduler.state == STATE_RUNNING
                and self.ready.is_set()
            )
            rows = {}
            for schedule_id in ids:
                job = self._scheduler.get_job(f"schedule_{schedule_id}") if running else None
                installed = self._installed.get(schedule_id, {})
                matching = job is not None and (
                    installed.get("trigger_identity") == _trigger_identity(job.trigger)
                    and tuple(job.args) == (schedule_id, installed.get("cadence_fingerprint"), self._generation)
                )
                rows[str(schedule_id)] = (
                    None
                    if job is None
                    else {
                        "installed_revision": installed.get("installed_revision") if matching else None,
                        "cadence_fingerprint": installed.get("cadence_fingerprint") if matching else None,
                        "schedule_id": schedule_id,
                        "next_run_at": job.next_run_time.isoformat()
                        if matching and job.next_run_time is not None
                        else None,
                        "effective_timezone": str(getattr(job.trigger, "timezone", self.timezone)),
                    }
                )
            return {
                "version": 1,
                "worker_id": self._worker_id,
                "generation": self._generation,
                "leader": self._leader,
                "scheduler_running": running,
                "observed_at": utc(self.clock()).isoformat(),
                "effective_timezone": self.timezone,
                "registrations": rows,
            }

    def request_stop(self):
        """Invalidate admission immediately; close() separately drains in-flight work."""
        with self._state_lock:
            self._stop.set()
            self._leader = False
            self._generation = None
            self.ready.clear()
        self._event.set()

    def close(self):
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self.request_stop()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()
        if self._engine is not None:
            self._engine.dispose()
