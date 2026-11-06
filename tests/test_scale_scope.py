"""Scope prompt regressions: native wrapper churn and modal re-entrancy."""
import importlib.util
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest

spec = importlib.util.spec_from_file_location(
    "scale_scope", "FuzzyCAD/tools/transform/fuzzycad_scale_scope.py")
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class Base:
    def notify(self, args):
        pass


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.primary = NS(entityToken="primary")
        self.neighbor = NS(entityToken="neighbor", edges=object())
        self.mark = {"id": 14, "factor": 1.2}
        self.group = NS(addBRepBody=Mock(side_effect=AssertionError("live BRep graphics forbidden")))
        self.ui = NS(messageBox=Mock(return_value="yes"))
        adsk = NS(core=NS(MessageBoxButtonTypes=NS(YesNoButtonType=1),
                          MessageBoxIconTypes=NS(QuestionIconType=1),
                          DialogResults=NS(DialogYes="yes")))
        self.session = {"gen": 1, "cmd": "scale"}
        self.m = NS(adsk=adsk, FuzzyInputChanged=Base, FuzzyPreview=Base,
                    run=lambda c: None, _active_cmd="scale", _cmd_session=self.session,
                    _pending={"body": self.primary}, _live={"scale": 14},
                    _find=lambda mid: self.mark, _follow_detect_dependents=lambda b: [self.neighbor],
                    _follow_all_tokens=lambda: ["primary", "neighbor"],
                    _group=Mock(return_value=self.group), _clear=Mock(),
                    _sample_edges=lambda edges: [[(0, 0, 0), (1, 0, 0)]], _sketchy=Mock(),
                    _crash_trace=Mock(), _ui=self.ui,
                    _app=NS(activeViewport=NS(refresh=Mock()), log=Mock()))
        scope.install(self.m)
        self.handler = self.m.FuzzyInputChanged()
        self.args = NS(input=NS(id="sel"))

    def test_yes_records_scope_and_clears_only_its_own_wireframe(self):
        self.handler.notify(self.args)
        self.assertEqual(self.mark["move_scope"], "together")
        self.assertEqual(self.mark["related_tokens"], ["neighbor"])
        self.assertEqual(self.mark["all_tokens_at_mark"], ["primary", "neighbor"])
        self.assertTrue(self.mark["scope_asked"])
        self.group.addBRepBody.assert_not_called()
        self.m._sketchy.assert_called_once()
        self.m._clear.assert_called_once_with("FuzzyCAD_ScaleScope_1")

    def test_no_keeps_only_primary_scope(self):
        self.ui.messageBox.return_value = "no"
        self.handler.notify(self.args)
        self.assertEqual(self.mark["move_scope"], "only")

    def test_modal_nested_input_does_not_rebuild_or_repeat_prompt(self):
        def modal(*args):
            self.handler.notify(self.args)
            return "yes"
        self.ui.messageBox.side_effect = modal
        self.handler.notify(self.args)
        self.ui.messageBox.assert_called_once()
        self.assertEqual(self.mark["move_scope"], "together")

    def test_answer_does_not_modify_a_successor_session(self):
        pending = {"move_scope": "only"}
        def modal(*args):
            self.m._cmd_session = {"gen": 2, "cmd": "scale"}
            self.m._pending = pending
            return "yes"
        self.ui.messageBox.side_effect = modal
        self.handler.notify(self.args)
        self.assertEqual(pending, {"move_scope": "only"})
        self.assertNotIn("move_scope", self.mark)
        self.m._clear.assert_called_once_with("FuzzyCAD_ScaleScope_1")

    def test_repeated_selection_does_not_ask_twice(self):
        self.handler.notify(self.args)
        self.handler.notify(self.args)
        self.ui.messageBox.assert_called_once()


if __name__ == "__main__":
    unittest.main()
