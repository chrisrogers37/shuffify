# F5 — Wire up Sentry in production

**Investigation:** [00_INVESTIGATION.md](00_INVESTIGATION.md)
**Targets:** RC8 (no production observability)
**Risk:** Low. Effort: Small.

## Context

`sentry-sdk>=2.58.0,<3.0` is already in `requirements/prod.txt:4` but is
**never imported or initialized** anywhere in the codebase (grep confirms
zero references outside requirements). `SENTRY_DSN` is not set in the DO
app spec. The DO log buffer is small, so historical loss events age out —
we can't tell how many WOOKLYN rotations have already silently failed.

This fix wires Sentry into the Flask app factory with Flask + APScheduler +
logging integrations, captures executor warnings (the existing
`rotate_executor.py:248-252` warning becomes a real event), and tags every
event with `schedule_id`, `playlist_id`, `job_type`, and `user_id` for
filtering.

## Files touched

| File | Change |
|------|--------|
| `config.py` | Add `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` to `Config` and `ProductionConfig` |
| `shuffify/__init__.py` | Initialize Sentry in `create_app` **before** any other extension setup |
| `shuffify/services/executors/base_executor.py` | In `_record_failure` and `_record_rollback` (F2), set Sentry scope tags |
| `documentation/production-database-setup.md` (or new doc) | Note Sentry DSN procurement & env-var name |
| `tests/test_sentry_init.py` | New test asserting graceful no-op when DSN is empty |

No code change is required in the executors themselves — the LoggingIntegration
captures `logger.warning(...)` calls at the configured threshold automatically.

## Approach

### A. Config

```python
# config.py — add to Config (or ProductionConfig if you want dev-off by default):

SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
SENTRY_ENVIRONMENT = os.environ.get("SENTRY_ENVIRONMENT", "production")
SENTRY_TRACES_SAMPLE_RATE = float(
    os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0")
)
SENTRY_PROFILES_SAMPLE_RATE = float(
    os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.0")
)
```

Tracing/profiling default off — Shuffify's traffic is low and we want this
fix to add zero perf risk. Errors + warnings come for free at any sample rate.

### B. Init in app factory

In `shuffify/__init__.py`'s `create_app` (or wherever the factory lives),
**before** Flask, db, scheduler, etc.:

```python
import logging

def create_app(config_name="production"):
    config_class = _select_config(config_name)
    dsn = getattr(config_class, "SENTRY_DSN", "") or ""
    if dsn:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=config_class.SENTRY_ENVIRONMENT,
            traces_sample_rate=config_class.SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=config_class.SENTRY_PROFILES_SAMPLE_RATE,
            send_default_pii=False,
            integrations=[
                FlaskIntegration(),
                SqlalchemyIntegration(),
                LoggingIntegration(
                    level=logging.INFO,         # breadcrumbs at INFO
                    event_level=logging.WARNING, # capture WARN+ as events
                ),
            ],
            release=_resolve_git_sha() or None,
            before_send=_strip_pii,
        )

    app = Flask(__name__)
    ...
```

`_resolve_git_sha`: read `GIT_SHA` env var (set in DO spec) or `git rev-parse`
in dev. `_strip_pii`: drop Spotify access tokens, encrypted refresh tokens,
session cookies from event extra/headers. Implement as a small helper that
walks `event.get("request", {}).get("headers", {})` and `event.get("extra", {})`
and redacts known keys.

### C. APScheduler context

Background jobs run outside the Flask request context. The `LoggingIntegration`
still works (logger events flow up to Sentry), but to **tag** scheduled jobs
properly, wrap the job-dispatch path:

```python
# In JobExecutorService.execute (base_executor.py:40), at the top:
import sentry_sdk

with sentry_sdk.configure_scope() as scope:
    scope.set_tag("schedule_id", schedule_id)
    if schedule:
        scope.set_tag("job_type", str(schedule.job_type))
        scope.set_tag("playlist_id", schedule.target_playlist_id)
        scope.set_user({"id": str(schedule.user_id)})
    # ...rest of execute
```

This makes Sentry's UI filterable by schedule, job type, playlist, and user.
Critical for finding the next WOOKLYN-class incident.

### D. Capture the existing warning

`rotate_executor.py:248-252`:

```python
logger.warning(
    "Schedule %s: %s size mismatch — expected %d tracks, got %d",
    schedule_id, phase, expected, actual,
)
```

With `LoggingIntegration(event_level=logging.WARNING)`, this becomes a Sentry
event automatically. After F1 ships, the warning is replaced by
`PlaylistVerificationError` (an exception) which Sentry captures as an
**error** event with full traceback — even better.

### E. DO app spec env

User action (not a code change):

```bash
doctl apps spec get 1ac416ce-832e-4f93-bcef-f0624c5f81c5 > do_spec.yaml
# Add under envs:
# - key: SENTRY_DSN
#   value: <from Sentry project settings>
#   type: SECRET
# - key: SENTRY_ENVIRONMENT
#   value: production
doctl apps update 1ac416ce-832e-4f93-bcef-f0624c5f81c5 --spec do_spec.yaml
```

(The DSN value is procured from the Sentry project's Client Keys page. **Do
not paste the actual DSN into this doc, the PR, or commit it to env files.**)

### F. PII handling

The Shuffify model includes encrypted refresh tokens and email addresses on
`User`. The `before_send` filter must:

- Strip `Authorization` / `Cookie` headers from any request payload.
- Strip the `session` extras.
- Replace `user.email` with a hash if it leaks into an event.

Recommended implementation: keep a denylist of substrings (e.g., `"refresh_token"`,
`"access_token"`, `"encrypted_"`, `"email"`) and redact any dict value whose
key matches.

## Tests (`tests/test_sentry_init.py`)

```python
def test_create_app_with_empty_dsn_does_not_init():
    """Empty/missing DSN must skip sentry_sdk.init."""

def test_create_app_with_dsn_calls_init(monkeypatch):
    """Patched sentry_sdk.init is invoked with expected kwargs."""

def test_before_send_strips_known_pii_keys():
    """before_send filter redacts refresh_token, access_token, etc."""

def test_before_send_passes_clean_events_through():
```

We do not need to call the real Sentry network from tests — patch `sentry_sdk`
in pytest.

## Verification

```bash
flake8 shuffify/__init__.py
pytest tests/test_sentry_init.py -v
```

Behavioral, post-deploy:

1. Trigger a deliberate executor warning (e.g., `flask shell` and call
   `logger.warning("test sentry capture")` from `shuffify.services.executors.rotate_executor`).
2. Confirm an event appears in the Sentry project within ~1 minute,
   tagged with the right environment.
3. After F1+F2 ship, trigger a synthetic `PlaylistVerificationError` and
   confirm it lands in Sentry with `schedule_id`/`playlist_id` tags.

## Rollout note

F5 is decoupled from F1-F4 and can ship first if desired — it makes the
**current** silent-loss class louder right now, even before the verifier
lands. Recommended order in `00_INVESTIGATION.md` puts F5 last only because
F1's exception is a more informative event than today's warning; but shipping
F5 first means we'll have observability while the other PRs are reviewed.

The DO env-var change is the user's responsibility; it does not require a
redeploy beyond the next push.
