"""Keep FuzzyCAD interaction choices inside the left FuzzyCAD Tools rail.

Fusion SelectionCommandInput objects remain as low-level selection plumbing,
because Fusion requires a focused selection input to receive picks.  User-facing
choices (Move together/separate and directional-scale scope) are sent into the
existing FuzzyCAD Tools palette and rendered inside its active-stage card.

There is no separate floating "FuzzyCAD Decision" palette anymore.  A choice is
staged locally and becomes active only after Confirm choice.  Switching directly
to another FuzzyCAD tool still terminates the current command through the normal
LaunchHandler path, so the existing fast workflow is preserved.
"""

import json

LEGACY_PROMPT_ID = "FuzzyCAD_DecisionPopup"


def install(m):
    adsk = m.adsk
    CurrentFuzzyCommandCreated = m.FuzzyCommandCreated
    CurrentPaletteHTMLHandler = m.PaletteHTMLHandler
    old_run = m.run
    old_stop = m.stop

    state = {"prompt": None}

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD INLINE PROMPT] " + msg)
        except Exception:
            pass

    def toolbar():
        try:
            return m._ui.palettes.itemById(m.TOOLBAR_ID) if m._ui else None
        except Exception:
            return None

    def cleanup_legacy_popup():
        """Remove the old floating decision palette if an earlier build left it."""
        try:
            if not m._ui:
                return
            p = m._ui.palettes.itemById(LEGACY_PROMPT_ID)
            if p is not None:
                p.deleteMe()
        except Exception:
            pass

    def push_prompt(payload=None):
        p = toolbar()
        if p is None:
            return
        try:
            p.sendInfoToHTML("decision_prompt", json.dumps(payload or {}))
        except Exception as exc:
            log("send inline prompt failed: {}".format(exc))

    def show_prompt(payload):
        state["prompt"] = dict(payload or {})
        push_prompt(state["prompt"])

    def hide_prompt(kind=None):
        cur = state.get("prompt")
        if kind and cur and cur.get("kind") != kind:
            return
        state["prompt"] = None
        push_prompt({})

    def set_hidden_radio(cid, index):
        try:
            it = m._inputs.itemById(cid) if m._inputs else None
            if it is not None and 0 <= index < it.listItems.count:
                it.listItems.item(index).isSelected = True
        except Exception:
            pass

    def move_choice(value):
        if getattr(m, "_active_cmd", None) != "transform" or not m._pending:
            return False
        value = "together" if value == "together" else "only"
        set_hidden_radio("moveScope", 1 if value == "together" else 0)
        m._pending["move_scope"] = value
        mid = m._live.get("move")
        mark = m._find(mid) if mid is not None else None
        if mark is not None:
            mark["move_scope"] = value
            mark["related_bodies"] = list(m._pending.get("related_bodies", []))
            m._clear(m.GROUP_PREVIEW)
            g = m._group(m.GROUP_PREVIEW)
            if g is not None:
                m._draw_one(g, mark)
            m._refresh_ghost(); m._send_state()
            try:
                m._app.activeViewport.refresh()
            except Exception:
                pass
        log("MOVE confirmed={} related={}".format(
            value, len(m._pending.get("related_bodies", []))))
        return True

    def scale_base(axis, side):
        body = m._pending.get("body") if m._pending else None
        c = list(m._pending.get("anchor", [0, 0, 0])) if m._pending else [0, 0, 0]
        if body is None or side == "both":
            return c
        bb = body.boundingBox
        idx = {"X": 0, "Y": 1, "Z": 2}[axis]
        mn = [bb.minPoint.x, bb.minPoint.y, bb.minPoint.z]
        mx = [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z]
        c[idx] = mn[idx] if side == "positive" else mx[idx]
        return c

    def reposition_scale_handles(side):
        if not m._pending or not m._inputs:
            return
        body = m._pending.get("body")
        if body is None:
            return
        bb = body.boundingBox
        c = list(m._pending["anchor"])
        mn = [bb.minPoint.x, bb.minPoint.y, bb.minPoint.z]
        mx = [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z]
        for axis in ("X", "Y", "Z"):
            it = m._inputs.itemById("ds" + axis)
            if it is None:
                continue
            idx = {"X": 0, "Y": 1, "Z": 2}[axis]
            origin = list(c)
            origin[idx] = mn[idx] if side == "negative" else mx[idx]
            vec = list(m._axis_unit(axis))
            if side == "negative":
                vec = [-vec[0], -vec[1], -vec[2]]
            try:
                it.setManipulator(adsk.core.Point3D.create(*origin),
                                  adsk.core.Vector3D.create(*vec))
                it.isVisible = True
                it.isEnabled = True
            except Exception:
                pass

    def scale_choice(value):
        if getattr(m, "_active_cmd", None) != "directional_scale" or not m._pending:
            return False
        if value not in ("positive", "negative", "both"):
            value = "positive"
        set_hidden_radio("dsSide", {"positive": 0, "negative": 1, "both": 2}[value])
        m._pending["scale_side"] = value
        reposition_scale_handles(value)
        mid = m._live.get("scale_axis")
        mark = m._find(mid) if mid is not None else None
        if mark is not None:
            axis = mark.get("axis", "X")
            mark["scale_side"] = value
            mark["base_anchor"] = scale_base(axis, value)
            m._clear(m.GROUP_PREVIEW)
            g = m._group(m.GROUP_PREVIEW)
            if g is not None:
                m._draw_one(g, mark)
            m._refresh_ghost(); m._send_state()
            try:
                m._app.activeViewport.refresh()
            except Exception:
                pass
        log("SCALE confirmed={}".format(value))
        return True

    class InlinePromptHTMLHandler(adsk.core.HTMLEventHandler):
        """Handle decisions emitted by toolbar.html, then delegate all other UI."""
        def __init__(self):
            super().__init__()
            self._delegate = CurrentPaletteHTMLHandler()

        def notify(self, args):
            action = None
            data = {}
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                action = e.action
                data = json.loads(e.data) if e.data else {}
            except Exception:
                pass

            if action == "decision_choice":
                cur = state.get("prompt")
                if cur and (not data.get("kind") or data.get("kind") == cur.get("kind")):
                    cur["selected"] = data.get("value", cur.get("selected"))
                return

            if action in ("decision_confirm", "choice"):
                cur = state.get("prompt") or {}
                kind = data.get("kind") or cur.get("kind")
                value = data.get("value")
                if value is None:
                    value = cur.get("selected")
                ok = False
                if kind == "move_scope":
                    ok = move_choice(value)
                elif kind == "scale_scope":
                    ok = scale_choice(value)
                if ok:
                    hide_prompt(kind)
                    log("CONFIRM kind={} value={} -> inline choice hidden".format(kind, value))
                return

            self._delegate.notify(args)

    m.PaletteHTMLHandler = InlinePromptHTMLHandler

    class WatchInput(adsk.core.InputChangedEventHandler):
        def __init__(self, cmd_name):
            super().__init__()
            self.cmd_name = cmd_name

        def notify(self, args):
            try:
                cid = args.input.id
                if self.cmd_name == "transform" and cid == "sel":
                    related = m._pending.get("related_bodies", []) if m._pending else []
                    if related:
                        show_prompt({
                            "kind": "move_scope",
                            "title": "Move together?",
                            "message": "Move the highlighted parts together or separately.",
                            "related_count": len(related),
                            "selected": m._pending.get("move_scope", "only"),
                            "options": [
                                {"value": "only", "label": "Separate", "glyph": "●"},
                                {"value": "together", "label": "Together", "glyph": "◎"},
                            ],
                        })
                    else:
                        hide_prompt("move_scope")
                elif self.cmd_name == "directional_scale" and cid == "dsb" and m._pending:
                    show_prompt({
                        "kind": "scale_scope",
                        "title": "Scale direction",
                        "message": "Choose which side moves, or scale symmetrically.",
                        "selected": m._pending.get("scale_side", "positive"),
                        "options": [
                            {"value": "positive", "label": "+ side", "glyph": "|→"},
                            {"value": "negative", "label": "- side", "glyph": "←|"},
                            {"value": "both", "label": "Both", "glyph": "↔"},
                        ],
                    })
            except Exception:
                log("watch input failed\n{}".format(m.traceback.format_exc()))

    class HideOnDestroy(adsk.core.CommandEventHandler):
        def __init__(self, kind):
            super().__init__()
            self.kind = kind

        def notify(self, args):
            hide_prompt(self.kind)

    class FuzzyCommandCreated(CurrentFuzzyCommandCreated):
        def notify(self, args):
            super().notify(args)
            try:
                inputs = args.command.commandInputs
                if self.cmd == "transform":
                    for cid in ("moveRelInfo", "moveScope"):
                        it = inputs.itemById(cid)
                        if it is not None:
                            it.isVisible = False
                    h = WatchInput("transform")
                    args.command.inputChanged.add(h)
                    m._handlers.append(h)
                    d = HideOnDestroy("move_scope")
                    args.command.destroy.add(d)
                    m._handlers.append(d)
                elif self.cmd == "directional_scale":
                    it = inputs.itemById("dsSide")
                    if it is not None:
                        it.isVisible = False
                    h = WatchInput("directional_scale")
                    args.command.inputChanged.add(h)
                    m._handlers.append(h)
                    d = HideOnDestroy("scale_scope")
                    args.command.destroy.add(d)
                    m._handlers.append(d)
            except Exception:
                log("command setup failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyCommandCreated = FuzzyCommandCreated

    def run(context):
        cleanup_legacy_popup()
        result = old_run(context)
        cleanup_legacy_popup()
        if state.get("prompt"):
            push_prompt(state["prompt"])
        log("INLINE DECISION UI READY: choices live inside FuzzyCAD Tools")
        return result

    def stop(context):
        hide_prompt()
        cleanup_legacy_popup()
        return old_stop(context)

    m.run = run
    m.stop = stop
