"""The test PostgreSQL fixture (tests/postgres_server.py): local only, and a missing server is a skip.

Each case runs a one-test suite in a nested pytest session (pytester), with ``-p`` loading the
fixture into it. None starts PostgreSQL: the integration tests that use the fixture do that.
"""

import pytest

URL_VARIABLE = "SHUFFIFY_TEST_POSTGRES_URL"


@pytest.mark.parametrize("url", [
    "postgresql://user@db.example.invalid/shuffify",
    "postgresql://user@10.0.0.5/shuffify",
    "postgresql:///shuffify?host=db.example.invalid",
])
def test_a_supplied_server_off_this_machine_is_refused(pytester, monkeypatch, url):
    monkeypatch.setenv(URL_VARIABLE, url)
    pytester.makepyfile(test_suite="def test_uses_postgres(postgres_url):\n    raise AssertionError('connected')\n")
    result = pytester.runpytest("-p", "tests.postgres_server")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*Use a local PostgreSQL test server*"])


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_a_supplied_local_server_is_used_as_given(pytester, monkeypatch, host):
    url = f"postgresql://user@{host}:5432/shuffify"
    monkeypatch.setenv(URL_VARIABLE, url)
    pytester.makepyfile(test_suite=f"def test_uses_postgres(postgres_url):\n    assert postgres_url == {url!r}\n")
    pytester.runpytest("-p", "tests.postgres_server").assert_outcomes(passed=1)


def test_without_a_server_the_tests_skip_and_a_fail_on_skip_run_fails(pytester, monkeypatch, tmp_path):
    monkeypatch.delenv(URL_VARIABLE, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # a directory without initdb
    pytester.makepyfile(test_suite="def test_uses_postgres(postgres_url):\n    pass\n")
    plugins = ("-p", "tests.postgres_server", "-p", "tests.fail_on_skip")
    assert pytester.runpytest(*plugins).ret == pytest.ExitCode.OK
    result = pytester.runpytest(*plugins, "--fail-on-skip")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["SKIPPED test_suite.py::test_uses_postgres: *PostgreSQL binaries*"])
