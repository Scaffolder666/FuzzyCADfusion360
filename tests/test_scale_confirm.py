"""Lifecycle routing regression checks. Run without Autodesk Fusion installed."""
import importlib.util
import json
import traceback
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    "safe_confirm", "FuzzyCAD/core/lifecycle/fuzzycad_safe_confirm.py")
safe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safe)


class Event:
    def add(self, handler):
        self.handler = handler


class ScaleConfirmTests(unittest.TestCase):
    def setUp(self):
        self.delegate = Mock()
        delegate = self.delegate

        class Palette:
            def notify(self, args):
                delegate(args)

        self.queue = []
        self.events = {}

        def register(name):
            self.events[name] = Event()
            return self.events[name]

        self.command = NS(isValid=True, doExecute=Mock(return_value=True))
        inputs = NS(command=self.command)
        core = NS(HTMLEventHandler=object, CustomEventHandler=object,
                  HTMLEventArgs=NS(cast=lambda x: x))
        self.m = NS(
            adsk=NS(core=core), run=lambda c: None, stop=lambda c: None,
            PaletteHTMLHandler=Palette, _crash_trace=Mock(), traceback=traceback,
            _active_cmd="scale", _active_edit_id=None, _inputs=inputs,
            _cmd_session={"gen": 17, "cmd": "scale", "inputs": inputs},
            _live={"scale": 14}, _clear=Mock(), GROUP_PREVIEW=123,
            _app=NS(fireCustomEvent=lambda name, payload: self.queue.append((name, payload)),
                    registerCustomEvent=register, unregisterCustomEvent=lambda name: None),
        )
        safe.install(self.m)
        self.m.run(None)
        self.palette = self.m.PaletteHTMLHandler()

    def confirm(self):
        self.palette.notify(NS(action="confirm", data="{}"))

    def deliver(self):
        name, payload = self.queue.pop(0)
        self.events[name].handler.notify(NS(additionalInfo=payload))

    def test_scale_confirm_is_deferred_and_preserves_live_preview(self):
        self.confirm()
        self.command.doExecute.assert_not_called()
        self.delegate.assert_not_called()
        self.m._clear.assert_not_called()
        self.deliver()
        self.command.doExecute.assert_called_once_with(True)
        self.assertEqual(self.m._live, {"scale": 14})
        self.m._clear.assert_not_called()

    def test_duplicate_clicks_are_coalesced(self):
        self.confirm()
        self.confirm()
        self.assertEqual(len(self.queue), 1)
        self.deliver()
        self.command.doExecute.assert_called_once()

    def test_new_scale_session_is_not_executed_by_old_request(self):
        self.confirm()
        self.m._cmd_session = dict(self.m._cmd_session, gen=18)
        self.deliver()
        self.command.doExecute.assert_not_called()

    def test_other_tool_is_not_executed_by_old_request(self):
        self.confirm()
        self.m._active_cmd = "axis_rotate"
        self.deliver()
        self.command.doExecute.assert_not_called()

    def test_fresh_native_input_wrapper_does_not_block_confirm(self):
        self.confirm()
        self.m._inputs = NS(command=self.command)
        self.assertIsNot(self.m._cmd_session["inputs"], self.m._inputs)
        self.deliver()
        self.command.doExecute.assert_called_once_with(True)

    def test_invalid_command_has_no_legacy_fallback(self):
        self.confirm()
        self.command.isValid = False
        self.deliver()
        self.command.doExecute.assert_not_called()
        self.delegate.assert_not_called()

    def test_missing_session_has_no_legacy_fallback(self):
        self.m._cmd_session = None
        self.confirm()
        self.assertFalse(self.queue)
        self.delegate.assert_not_called()

    def test_dispatch_failure_has_no_legacy_fallback_and_can_retry(self):
        original = self.m._app.fireCustomEvent
        self.m._app.fireCustomEvent = Mock(side_effect=RuntimeError("dispatch"))
        self.confirm()
        self.delegate.assert_not_called()
        self.m._app.fireCustomEvent = original
        self.confirm()
        self.deliver()
        self.command.doExecute.assert_called_once()

    def test_execution_failure_logs_and_allows_retry(self):
        self.command.doExecute.side_effect = RuntimeError("native API error")
        self.confirm()
        self.deliver()
        self.assertTrue(any(c.args[0] == "SAFE_CREATE_FINISH_EXCEPTION"
                            for c in self.m._crash_trace.call_args_list))
        self.command.doExecute.side_effect = None
        self.confirm()
        self.deliver()
        self.assertEqual(self.command.doExecute.call_count, 2)
        self.delegate.assert_not_called()

    def test_false_result_is_logged_without_legacy_fallback(self):
        self.command.doExecute.return_value = False
        self.confirm()
        self.deliver()
        self.assertTrue(any("ok=False" in str(c) for c in self.m._crash_trace.call_args_list))
        self.delegate.assert_not_called()

    def test_ordinary_other_tool_confirm_keeps_existing_routing(self):
        self.m._active_cmd = "note"
        self.confirm()
        self.delegate.assert_called_once()
        self.assertFalse(self.queue)

    def test_all_creation_tools_use_deferred_safe_confirm(self):
        for tool in safe.CREATE_TOOLS:
            with self.subTest(tool=tool):
                self.m._active_cmd = tool
                self.m._cmd_session["cmd"] = tool
                self.command.doExecute.reset_mock()
                self.confirm()
                self.command.doExecute.assert_not_called()
                self.deliver()
                self.command.doExecute.assert_called_once_with(True)
        self.delegate.assert_not_called()

    def test_live_accept_and_reject_settle_before_resolving(self):
        for action in ("accept", "reject"):
            with self.subTest(action=action):
                self.command.doExecute.reset_mock()
                self.palette.notify(NS(action=action, data='{"id":14}'))
                self.delegate.assert_not_called()
                self.command.doExecute.assert_not_called()
                self.deliver()
                self.command.doExecute.assert_called_once_with(True)
                self.assertEqual(self.m._live, {"scale": 14})

    def test_synchronous_close_requires_destroy_to_release_ownership(self):
        self.assertFalse(self.m._close_active_create_sync("toolbar-switch"))
        self.assertEqual(self.m._active_cmd, "scale")

    def test_synchronous_close_success_after_destroy(self):
        def close(_terminate):
            self.m._active_cmd = None
            self.m._cmd_session = None
            return True
        self.command.doExecute.side_effect = close
        self.assertTrue(self.m._close_active_create_sync("clear-all"))

    def test_synchronous_close_false_or_exception_keeps_ownership(self):
        self.command.doExecute.return_value = False
        self.assertFalse(self.m._close_active_create_sync())
        self.command.doExecute.side_effect = RuntimeError("cannot finish")
        self.assertFalse(self.m._close_active_create_sync())
        self.assertEqual(self.m._active_cmd, "scale")

    def test_old_reopen_confirm_does_not_close_new_session_of_same_mark(self):
        self.m._active_cmd = "edit_existing"
        self.m._active_edit_id = 12
        self.m._active_edit_generation = 2
        self.confirm()
        self.m._active_edit_generation = 3
        self.deliver()
        self.command.doExecute.assert_not_called()

    def test_reopen_rejected_execution_does_not_verify_or_resolve(self):
        self.m._active_cmd = "edit_existing"
        self.m._active_edit_id = 12
        self.command.doExecute.return_value = False
        self.confirm()
        self.deliver()
        self.assertTrue(any(c.args[0] == "SAFE_FINISH_NOT_CLOSED"
                            for c in self.m._crash_trace.call_args_list))

    def test_reopened_confirm_keeps_existing_safe_routing(self):
        self.m._active_cmd = "edit_existing"
        self.m._active_edit_id = 12
        self.confirm()
        self.delegate.assert_not_called()
        self.assertEqual(json.loads(self.queue[0][1]), {"reason": "confirm", "id": 12})


if __name__ == "__main__":
    unittest.main()
