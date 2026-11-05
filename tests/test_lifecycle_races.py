"""Exercise teardown ownership with lightweight Fusion event doubles."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest


def load_inner(path, name, env, kind=ast.ClassDef):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, kind) and n.name == name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 path, "exec"), env)
    return env[name]


class LifecycleRaceTests(unittest.TestCase):
    def setUp(self):
        self.old = {"gen": 1, "live": {"scale_axis": 14}}
        self.new = {"gen": 2, "live": {"axis_rotate": 15}}
        self.m = NS(_cmd_session=self.new, _active_cmd="axis_rotate", _inputs=object(),
                    _pending={"new": True}, _live=self.new["live"], _switching=False,
                    _clear=Mock(), _redraw_marks=Mock(), _send_state=Mock(),
                    _find=lambda mid: {"factor": 1.2, "angle": 20},
                    _remove_mark=Mock(), GROUP_PREVIEW=123,
                    _ui=NS(terminateActiveCommand=Mock()))
        core = NS(CommandEventHandler=object, InputChangedEventHandler=object,
                  ValidateInputsEventHandler=object, CustomEventHandler=object)
        self.adsk = NS(core=core)
        self.m.adsk = self.adsk

    def test_late_custom_tool_callbacks_do_not_touch_successor(self):
        names = [prefix + suffix for prefix in ("DirectionalScale", "AxisRotate")
                 for suffix in ("InputChanged", "Preview", "Execute", "Destroy")]
        for name in names:
            with self.subTest(handler=name):
                env = {"m": self.m, "adsk": self.adsk}
                cls = load_inner("FuzzyCAD/tools/fuzzycad_direct_interactions.py", name, env)
                cls(self.old).notify(NS())
                self.assertIs(self.m._cmd_session, self.new)
                self.assertEqual(self.m._live, {"axis_rotate": 15})
                self.assertEqual(self.m._pending, {"new": True})
                self.m._clear.assert_not_called()
                self.m._remove_mark.assert_not_called()

    def test_custom_destroy_does_not_clear_reopened_edit_with_old_session_token(self):
        self.m._cmd_session = self.old
        self.m._active_cmd = "edit_existing"
        self.m._active_edit_id = 14
        self.old["cmd"] = "axis_rotate"
        env = {"m": self.m, "adsk": self.adsk}
        cls = load_inner("FuzzyCAD/tools/fuzzycad_direct_interactions.py",
                         "AxisRotateDestroy", env)
        cls(self.old).notify(NS())
        self.assertEqual(self.m._active_cmd, "edit_existing")
        self.assertEqual(self.m._active_edit_id, 14)
        self.m._clear.assert_not_called()

    def test_standard_destroy_does_not_delete_mark_inherited_by_reopened_edit(self):
        remove = Mock()
        env = {"adsk": self.adsk, "_cmd_session": self.old,
               "_active_cmd": "edit_existing", "_remove_mark": remove}
        cls = load_inner("FuzzyCAD/FuzzyCAD_legacy.py", "FuzzyDestroy", env)
        cls(self.old).notify(NS())
        self.assertEqual(env["_active_cmd"], "edit_existing")
        remove.assert_not_called()

    def test_current_standard_destroy_clears_fresh_input_wrapper(self):
        session = {"gen": 1, "cmd": "scale", "inputs": object(), "live": {"scale": 14}}
        env = {"adsk": self.adsk, "_cmd_session": session,
               "_active_cmd": "scale", "_inputs": object(), "_pending": {},
               "_live": session["live"], "_switching": False,
               "_find": lambda mid: {"factor": 1.2}, "_is_default": lambda cat, mark: False,
               "_remove_mark": Mock(), "_clear": Mock(), "GROUP_PREVIEW": 1,
               "_redraw_marks": Mock(), "_send_state": Mock()}
        cls = load_inner("FuzzyCAD/FuzzyCAD_legacy.py", "FuzzyDestroy", env)
        cls(session).notify(NS())
        self.assertIsNone(env["_inputs"])
        self.assertIsNone(env["_active_cmd"])
        env["_remove_mark"].assert_not_called()

    def test_current_custom_destroy_releases_ownership(self):
        for name in ("DirectionalScaleDestroy", "AxisRotateDestroy"):
            with self.subTest(handler=name):
                self.old["cmd"] = "directional_scale" if name.startswith("Directional") else "axis_rotate"
                self.m._active_cmd = self.old["cmd"]
                self.m._cmd_session = self.old
                self.m._live = {"scale_axis": 14, "axis_rotate": 15}
                env = {"m": self.m, "adsk": self.adsk}
                cls = load_inner("FuzzyCAD/tools/fuzzycad_direct_interactions.py", name, env)
                cls(self.old).notify(NS())
                self.assertIsNone(self.m._cmd_session)
                self.assertIsNone(self.m._active_cmd)
                self.assertIsNone(self.m._inputs)
                self.assertEqual(self.m._live, {})
                self.m._remove_mark.assert_not_called()

    def test_late_directional_scope_input_is_ignored(self):
        env = {"m": self.m, "adsk": self.adsk}
        cls = load_inner("FuzzyCAD/tools/transform/fuzzycad_directional_scale_scope.py",
                         "ScopeInputChanged", env)
        cls(self.old).notify(NS())
        self.m._clear.assert_not_called()

    def test_previous_tool_post_destroy_does_not_redraw_over_successor(self):
        finish = dict(deferred=True, command="scale", mark_id=14, session=self.old)
        render = Mock()
        env = {"m": self.m, "custom_finish": finish, "trace": Mock(), "render": render}
        cls = load_inner("FuzzyCAD/core/render/fuzzycad_visual_transition.py",
                         "PostCustomToolDestroy", env)
        cls("scale", self.old).notify(NS())
        render.assert_not_called()
        self.assertFalse(finish["deferred"])
        self.assertIs(self.m._cmd_session, self.new)

    def test_post_destroy_never_discards_successor_deferred_state(self):
        finish = dict(deferred=True, command="axis_rotate", mark_id=15, session=self.new)
        env = {"m": self.m, "custom_finish": finish, "trace": Mock(), "render": Mock()}
        cls = load_inner("FuzzyCAD/core/render/fuzzycad_visual_transition.py",
                         "PostCustomToolDestroy", env)
        cls("scale", self.old).notify(NS())
        self.assertIs(finish["session"], self.new)
        self.assertTrue(finish["deferred"])

    def test_current_post_destroy_renders_proposed_mark(self):
        self.m._active_cmd = None
        self.m._mark_phase = lambda mark: "proposed"
        finish = dict(deferred=True, command="scale", mark_id=14, session=self.old)
        render = Mock()
        env = {"m": self.m, "custom_finish": finish, "trace": Mock(), "render": render}
        cls = load_inner("FuzzyCAD/core/render/fuzzycad_visual_transition.py",
                         "PostCustomToolDestroy", env)
        cls("scale", self.old).notify(NS())
        render.assert_called_once_with(reason="scale-post-destroy")
        self.assertFalse(finish["deferred"])

    def test_stale_compare_callbacks_leave_new_session_untouched(self):
        state = dict(generation=2, inputs=object(), pending=(1, 2, 3), finishing=True)
        snapshot = state.copy()
        for name in ("InputChanged", "Destroy", "Execute", "Validate"):
            with self.subTest(handler=name):
                env = {"state": state, "adsk": self.adsk}
                cls = load_inner("FuzzyCAD/compare/fuzzycad_compare_stable.py", name, env)
                cls(1).notify(NS())
                self.assertEqual(state, snapshot)

    def test_stale_compare_finish_does_not_consume_new_request(self):
        state = dict(generation=2, pending=(1, 2, 3), finishing=True, inputs=None)
        create = Mock()
        env = {"m": self.m, "state": state, "adsk": self.adsk,
               "log": Mock(), "create_mark": create}
        cls = load_inner("FuzzyCAD/compare/fuzzycad_compare_stable.py", "Finish", env)
        cls().notify(NS(additionalInfo="1"))
        self.assertEqual(state["pending"], (1, 2, 3))
        self.assertTrue(state["finishing"])
        create.assert_not_called()
        self.m._ui.terminateActiveCommand.assert_not_called()

    def test_compare_finish_never_terminates_new_tool(self):
        state = dict(generation=2, pending=(1, 2, 3, [], []), finishing=True, inputs=None)
        create = Mock()
        env = {"m": self.m, "state": state, "adsk": self.adsk,
               "log": Mock(), "create_mark": create}
        cls = load_inner("FuzzyCAD/compare/fuzzycad_compare_stable.py", "Finish", env)
        cls().notify(NS(additionalInfo="2"))
        create.assert_called_once_with(1, 2, 3, [], [])
        self.m._ui.terminateActiveCommand.assert_not_called()

    def test_tool_switch_uses_native_preemption_even_without_valid_selection(self):
        for source in ("scale", "directional_scale", "axis_rotate", "extrude"):
            with self.subTest(source=source):
                successor = NS(execute=Mock())
                ui = NS(commandDefinitions=NS(itemById=lambda cid: successor),
                        terminateActiveCommand=Mock())
                close = Mock(return_value=False)
                env = {"adsk": self.adsk, "_ui": ui, "_active_cmd": source,
                       "_switching": False, "_safe_create_tools": {source},
                       "_close_active_create_sync": close}
                cls = load_inner("FuzzyCAD/FuzzyCAD_legacy.py", "LaunchHandler", env)
                cls().notify(NS(additionalInfo="FuzzyCAD_Successor"))
                successor.execute.assert_called_once()
                ui.terminateActiveCommand.assert_not_called()
                close.assert_not_called()
                self.assertFalse(env["_switching"])

    def test_failed_reopen_close_does_not_launch_commit(self):
        successor = NS(execute=Mock())
        ui = NS(commandDefinitions=NS(itemById=lambda cid: successor),
                terminateActiveCommand=Mock())
        env = {"adsk": self.adsk, "_ui": ui, "_active_cmd": "edit_existing",
               "_switching": False, "_close_active_edit_sync": Mock(return_value=False),
               "_pending_commit_id": 14}
        cls = load_inner("FuzzyCAD/FuzzyCAD_legacy.py", "LaunchHandler", env)
        cls().notify(NS(additionalInfo="FuzzyCAD_Commit"))
        successor.execute.assert_not_called()
        ui.terminateActiveCommand.assert_not_called()
        self.assertIsNone(env["_pending_commit_id"])

    def test_clear_all_aborts_if_creation_command_does_not_close(self):
        self.m._safe_create_tools = {"axis_rotate"}
        self.m._close_active_create_sync = Mock(return_value=False)
        env = {"m": self.m, "log": Mock()}
        close = load_inner("FuzzyCAD/core/lifecycle/fuzzycad_clear_all.py",
                           "finish_active_command_on_main_thread", env, ast.FunctionDef)
        self.assertFalse(close())
        self.assertIs(self.m._cmd_session, self.new)
        self.m._ui.terminateActiveCommand.assert_not_called()


if __name__ == "__main__":
    unittest.main()
