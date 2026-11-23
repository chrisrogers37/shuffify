"""Every kind that admission accepts has a worker handler, and the direct routine maps name the same kinds."""

import ast
import pathlib

from shuffify.services.live_review_scope import DIRECT_STEPS
from shuffify.services.operations.commands.registry import COMMAND_HANDLERS
from shuffify.services.operations.commands.routine_capture import DIRECT_MODELS

PACKAGE = pathlib.Path(__file__).resolve().parents[3] / "shuffify"
# The calls that admit a kind, and the position of their kind argument (None:
# by keyword only). admit_direct takes a direct routine kind, and admits the
# kind of its DIRECT_MODELS command.
ADMISSIONS = {"enqueue": None, "enqueue_command": 1, "admit_asset": 2, "admit_finalizer": 2, "admit_direct": 2}


def _called_name(call):
    return call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)


def _kinds(call, position):
    """The strings in a call's kind argument, or None when the call passes no kind."""
    if position is not None and position < len(call.args):
        argument = call.args[position]
    else:
        argument = next((keyword.value for keyword in call.keywords if keyword.arg == "kind"), None)
    if argument is None:
        return None
    return {leaf.value for leaf in ast.walk(argument)
            if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str)}


def _admission_calls():
    """Locate admission calls even when their arguments are forwarded through **kwargs."""
    for path in sorted(PACKAGE.rglob("*.py")):
        for statement in ast.parse(path.read_text(encoding="utf-8")).body:
            for call in ast.walk(statement):
                name = _called_name(call) if isinstance(call, ast.Call) else None
                if name in ADMISSIONS:
                    yield path.relative_to(PACKAGE).as_posix(), getattr(statement, "name", "<module>"), name, call


def test_every_admitted_kind_has_a_worker_handler():
    admissions = [
        (definition, name, _kinds(call, ADMISSIONS[name]) or set())
        for _, definition, name, call in _admission_calls()
    ]
    # These admit a kind their caller chose, so the callers' kinds are read instead.
    assert sorted(definition for definition, _, kinds in admissions if not kinds) == [
        "admit_asset", "admit_direct", "admit_finalizer", "enqueue_command",
    ]
    direct = {kind for _, name, kinds in admissions if name == "admit_direct" for kind in kinds}
    assert direct - DIRECT_MODELS.keys() == set()
    admitted = {kind for _, name, kinds in admissions if name != "admit_direct" for kind in kinds}
    admitted |= {DIRECT_MODELS[kind].kind for kind in direct}
    assert admitted - COMMAND_HANDLERS.keys() == set()


def test_only_interactive_and_scheduled_admission_call_enqueue():
    # A new route or service calling the repository directly would bypass
    # the shared HTTP limit in enqueue_command. Scheduled fires are exempt.
    assert sorted((path, definition) for path, definition, name, _ in _admission_calls() if name == "enqueue") == [
        ("routes/queue.py", "enqueue_command"),
        ("services/scheduled_admission.py", "admit_scheduled"),
    ]


def test_the_direct_routine_maps_name_the_same_kinds():
    # Admission, capture and the worker read a direct kind's command from
    # DIRECT_MODELS, and its review reads its steps from DIRECT_STEPS.
    assert set(DIRECT_MODELS) == set(DIRECT_STEPS)
