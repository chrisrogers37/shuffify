"""``--fail-on-skip``: a run in which anything was skipped fails.

CI chooses what to run, with markers and paths in the workflow, and everything
it chooses must run. A skip there is a missing precondition, such as a database
server or a tool that is not installed, never a test that does not apply, so CI
fails on it. Local runs may still skip, which is why the flag is opt-in.

With the flag, a run that would exit OK exits TESTS_FAILED when a test or a
collector was skipped, and the terminal summary lists each skip with its
reason. A collector skip is a module-level ``pytest.skip`` or
``pytest.importorskip``. Any other exit status is left as it was: failures, an
interruption, a usage error and a run that collected nothing.

Not skips: tests that never ran because they were deselected or ignored
(``-m``, ``-k``, ``--deselect``, ``--ignore``, or a plugin such as
pytest-split), and expected failures, which pytest reports with outcome
"skipped" and a ``wasxfail`` attribute.

``tests/conftest.py`` registers this module (``pytest_plugins``). Without the
flag no hook is registered, so nothing changes.
"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--fail-on-skip",
        action="store_true",
        default=False,
        help="Exit with status 1 if any test or collector was skipped, and list each skip in the terminal summary.",
    )


def pytest_configure(config):
    if config.getoption("fail_on_skip"):
        config.pluginmanager.register(FailOnSkip(), "fail-on-skip")


def _reason(report) -> str:
    """Why a report was skipped, without the "Skipped: " pytest puts in front of it."""
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:  # (path, line number, message)
        longrepr = longrepr[2]
    return str(longrepr or "").removeprefix("Skipped: ").strip() or "no reason given"


class FailOnSkip:
    """The skips of one run, registered only when ``--fail-on-skip`` is given."""

    def __init__(self):
        self.skips = []  # (what was skipped, why), in the order they were reported
        self.changed_status = False

    def pytest_runtest_logreport(self, report):
        if report.skipped and not hasattr(report, "wasxfail"):
            self.skips.append((report.nodeid, _reason(report)))

    def pytest_collectreport(self, report):
        if report.skipped:
            self.skips.append((f"{report.nodeid} (collection)", _reason(report)))

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session):
        if self.skips and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            self.changed_status = True

    @pytest.hookimpl(trylast=True)  # after the coverage table, just above pytest's own summary
    def pytest_terminal_summary(self, terminalreporter):
        if not self.skips:
            return
        terminalreporter.write_sep("=", "skipped (--fail-on-skip)", red=True, bold=True)
        for what, reason in self.skips:
            terminalreporter.write_line(f"SKIPPED {what}: {reason}")
        count = f"{len(self.skips)} skipped"
        if self.changed_status:
            terminalreporter.write_line(f"--fail-on-skip: {count}, so the run fails", red=True)
        else:
            terminalreporter.write_line(f"--fail-on-skip: {count}; the exit status stays as it was", yellow=True)
