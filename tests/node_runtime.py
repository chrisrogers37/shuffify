"""Run shipped-JavaScript scenarios under Node, requiring async bodies to finish."""

import subprocess

DONE = "__RUNTIME_SCENARIO_FINISHED__"
_TAIL = "})().catch("


def run_scenario(node, script, cwd):
    """Run one scenario and fail unless its final async body finishes.

    Node exits 0 when a scenario awaits a promise that never settles, skipping
    every later assertion. Mark the end of the last async body so that case
    fails instead of passing silently.
    """
    index = script.rfind(_TAIL)
    # An async body with an unrecognized ending would otherwise skip the finish check.
    assert index >= 0 or "(async () =>" not in script, "async scenarios must end with '})().catch('"
    if index >= 0:
        script = (script[:index] + "})().then(() => console.log('" + DONE + "')).catch("
                  + script[index + len(_TAIL):])
    # Through stdin, not -e: Linux limits one argument to 128 KiB, and a rendered page plus
    # its scripts can exceed that (macOS has no such per-argument limit).
    result = subprocess.run([node, "-"], input=script, cwd=cwd, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr or result.stdout
    if index >= 0:
        assert DONE in result.stdout, "scenario did not finish: " + (result.stderr or result.stdout)
    return result
