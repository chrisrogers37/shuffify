"""``--fail-on-skip`` (tests/fail_on_skip.py): a run that skipped anything fails.

Each case runs a small suite in a nested pytest session (pytester). That session
does not read tests/conftest.py, so ``-p`` loads the plugin into it.
"""

import pytest

OK, TESTS_FAILED = pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED

SKIPPING_SUITE = """
    import pytest

    def test_runs():
        pass

    @pytest.mark.parametrize("server", ["primary", "replica"])
    def test_needs_a_server(server):
        pytest.skip("no database server on PATH")
"""


def run(pytester, *args):
    return pytester.runpytest("-p", "tests.fail_on_skip", *args)


def test_a_skipped_test_fails_the_run_and_every_skip_is_listed_with_its_reason(pytester):
    pytester.makepyfile(test_suite=SKIPPING_SUITE)
    result = run(pytester, "--fail-on-skip")
    assert result.ret == TESTS_FAILED
    result.assert_outcomes(passed=1, skipped=2)
    result.stdout.fnmatch_lines([
        "*skipped (--fail-on-skip)*",
        "SKIPPED test_suite.py::test_needs_a_server[[]primary]: no database server on PATH",
        "SKIPPED test_suite.py::test_needs_a_server[[]replica]: no database server on PATH",
        "--fail-on-skip: 2 skipped, so the run fails",
    ])


def test_the_same_suite_passes_without_the_flag(pytester):
    pytester.makepyfile(test_suite=SKIPPING_SUITE)
    result = run(pytester)
    assert result.ret == OK
    result.assert_outcomes(passed=1, skipped=2)
    result.stdout.no_fnmatch_line("*--fail-on-skip*")


SKIP_MECHANISMS = {
    "runtime": "def test_it():\n    pytest.skip('why')",
    "mark": "@pytest.mark.skip(reason='why')\ndef test_it():\n    pass",
    "skipif": "@pytest.mark.skipif(True, reason='why')\ndef test_it():\n    pass",
    "fixture": "@pytest.fixture\ndef thing():\n    pytest.skip('why')\n\ndef test_it(thing):\n    pass",
    "importorskip": "def test_it():\n    pytest.importorskip('no_such_module_anywhere')",
}


@pytest.mark.parametrize("body", SKIP_MECHANISMS.values(), ids=SKIP_MECHANISMS)
def test_every_way_of_skipping_a_test_counts(pytester, body):
    pytester.makepyfile(test_suite="import pytest\n\n" + body + "\n")
    result = run(pytester, "--fail-on-skip")
    assert result.ret == TESTS_FAILED
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["SKIPPED test_suite.py::test_it: *"])


def test_a_suite_without_skips_passes_with_the_flag(pytester):
    pytester.makepyfile(test_suite="def test_a():\n    pass\n\ndef test_b():\n    pass\n")
    result = run(pytester, "--fail-on-skip")
    assert result.ret == OK
    result.assert_outcomes(passed=2)
    result.stdout.no_fnmatch_line("*--fail-on-skip*")


MODULE_SKIPS = {
    "skip": "pytest.skip('tool is not installed', allow_module_level=True)",
    "importorskip": "pytest.importorskip('no_such_module_anywhere', reason='tool is not installed')",
}


@pytest.mark.parametrize("statement", MODULE_SKIPS.values(), ids=MODULE_SKIPS)
def test_a_module_skipped_at_collection_fails_the_run(pytester, statement):
    pytester.makepyfile(test_ok="def test_a():\n    pass\n")
    pytester.makepyfile(test_tool=f"import pytest\n\n{statement}\n\ndef test_never():\n    pass\n")
    assert run(pytester).ret == OK
    result = run(pytester, "--fail-on-skip")
    assert result.ret == TESTS_FAILED
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["SKIPPED test_tool.py (collection): tool is not installed"])


# A conftest that drops a group's tests the way pytest-split does.
SPLIT_LIKE = """
    def pytest_collection_modifyitems(config, items):
        dropped = [item for item in items if item.name == "test_skips"]
        items[:] = [item for item in items if item not in dropped]
        config.hook.pytest_deselected(items=dropped)
"""
# (command line options, conftest.py): each leaves the one skipping test unrun.
DESELECTIONS = {
    "k": (["-k", "test_runs"], ""),
    "m": (["-m", "not slow"], ""),
    "deselect": (["--deselect", "test_skipping.py::test_skips"], ""),
    "ignore": (["--ignore", "test_skipping.py"], ""),
    "split": ([], SPLIT_LIKE),
}


@pytest.mark.parametrize("options, conftest", DESELECTIONS.values(), ids=DESELECTIONS)
def test_tests_that_never_ran_are_not_skips(pytester, options, conftest):
    pytester.makeini("[pytest]\nmarkers =\n    slow: a slow test\n")
    pytester.makeconftest(conftest)
    pytester.makepyfile(test_suite="def test_runs():\n    pass\n")
    pytester.makepyfile(
        test_skipping="import pytest\n\n@pytest.mark.slow\ndef test_skips():\n    pytest.skip('would skip if it ran')\n"
    )
    assert run(pytester, *options).parseoutcomes().get("skipped", 0) == 0  # the options do leave it unrun
    result = run(pytester, "--fail-on-skip", *options)
    assert result.ret == OK
    result.stdout.no_fnmatch_line("*--fail-on-skip*")


def test_expected_failures_are_not_skips(pytester):
    """pytest reports an expected failure with outcome "skipped", marked ``wasxfail``."""
    pytester.makepyfile(test_suite="""
        import pytest

        @pytest.mark.xfail(reason="known bug")
        def test_marked():
            assert False

        def test_called():
            pytest.xfail("known bug")
    """)
    result = run(pytester, "--fail-on-skip")
    assert result.ret == OK
    result.assert_outcomes(xfailed=2)
    result.stdout.no_fnmatch_line("*--fail-on-skip*")


def test_a_failing_suite_keeps_its_failure_status(pytester):
    pytester.makepyfile(test_suite="""
        import pytest

        def test_fails():
            assert False

        def test_skips():
            pytest.skip("why")
    """)
    result = run(pytester, "--fail-on-skip")
    assert result.ret == TESTS_FAILED
    result.assert_outcomes(failed=1, skipped=1)
    result.stdout.fnmatch_lines(["SKIPPED test_suite.py::test_skips: why"])


# The other statuses a run that did not finish OK can have. A skip must not rewrite them.
OTHER_STATUSES = {
    "interrupted": (
        """
        import pytest

        def test_skips():
            pytest.skip("why")

        def test_stops_the_run():
            pytest.exit("stop here")
        """,
        pytest.ExitCode.INTERRUPTED,
    ),
    "nothing collected": (
        "import pytest\n\npytest.skip('why', allow_module_level=True)\n",
        pytest.ExitCode.NO_TESTS_COLLECTED,
    ),
}


@pytest.mark.parametrize("source, status", OTHER_STATUSES.values(), ids=OTHER_STATUSES)
def test_other_statuses_are_left_as_they_were(pytester, source, status):
    pytester.makepyfile(test_suite=source)
    assert run(pytester).ret == status
    assert run(pytester, "--fail-on-skip").ret == status
