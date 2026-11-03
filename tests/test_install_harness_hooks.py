"""`synapptic install` registers the harness shims in settings.json: one entry per event and matcher, running
the core through the interpreter that holds it, next to whatever else those events already run; `uninstall`
removes exactly those entries."""

import sys

from synapptic.cli import HARNESS_HOOKS, register_harness_hook, unregister_harness_hooks

OTHER = {"matcher": "*", "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/other-guard.py"}]}


def settings_with_other():
    return {"Stop": [dict(OTHER)]}


def test_install_registers_one_shim_per_event_and_matcher():
    hooks = settings_with_other()
    assert all(register_harness_hook(hooks, event, matcher, role) for event, matcher, role in HARNESS_HOOKS)
    registered = {(event, entry["matcher"]) for event, entries in hooks.items() for entry in entries}
    assert {(event, matcher) for event, matcher, _ in HARNESS_HOOKS} <= registered
    stop_commands = [h["command"] for entry in hooks["Stop"] for h in entry["hooks"]]
    assert any("other-guard.py" in command for command in stop_commands)
    assert any(command == f'{sys.executable} -c "import synapptic_core as c; c.guard()"' for command in stop_commands)
    # a second install adds nothing
    assert not any(register_harness_hook(hooks, event, matcher, role) for event, matcher, role in HARNESS_HOOKS)


def test_uninstall_removes_only_the_shims():
    hooks = settings_with_other()
    for event, matcher, role in HARNESS_HOOKS:
        register_harness_hook(hooks, event, matcher, role)
    touched = unregister_harness_hooks(hooks)
    assert set(touched) == {event for event, _, _ in HARNESS_HOOKS}
    assert hooks["Stop"] == [OTHER] and hooks["UserPromptSubmit"] == [] and hooks["PreToolUse"] == []
