"""The worker constructs service dependencies without starting a web scheduler."""


def test_app_factory_can_leave_scheduler_initialization_to_worker(monkeypatch):
    from config import TestConfig
    from shuffify import create_app

    calls = []
    monkeypatch.setattr(TestConfig, "SCHEDULER_ENABLED", True)
    monkeypatch.setattr("shuffify.scheduler.init_scheduler", lambda app: calls.append(app))
    app = create_app("testing", start_scheduler=False)
    assert calls == []
    assert "scheduler" not in app.extensions
