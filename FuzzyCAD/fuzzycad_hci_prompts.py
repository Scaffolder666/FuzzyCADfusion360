"""Move user-facing interaction choices out of Fusion's native command panel.

Fusion SelectionCommandInput objects remain as low-level selection plumbing,
because Fusion requires a visible focused selection input to receive picks.
Move-scope and directional-scale decisions are rendered in a dedicated FuzzyCAD
HTML popup palette.
"""

import json

PROMPT_ID = "FuzzyCAD_DecisionPopup"
PROMPT_URL = "palette/prompt.html"


def install(m):
    adsk = m.adsk
    CurrentFuzzyCommandCreated = m.FuzzyCommandCreated
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
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD POPUP] " + msg)
        except Exception:
            pass

    def prompt_palette(create=True):
        if not m._ui:
            return None
        p = m._ui.palettes.itemById(PROMPT_ID)
        if p is None and create:
            p = m._ui.palettes.add(PROMPT_ID, "FuzzyCAD Decision", PROMPT_URL,
                                   True, True, True, 330, 185)
            try:
                p.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateFloating
            except Exception:
                pass
            h = PromptHTMLHandler()
            p.incomingFromHTML.add(h); m._handlers.append(h)
        return p

    def show_prompt(payload):
        state["prompt"] = payload
        p = prompt_palette(True)
        if p is None:
            return
        p.isVisible = True
        try:
            p.sendInfoToHTML("prompt", json.dumps(payload))
        except Exception as exc:
            log("send prompt failed: {}".format(exc))

    def hide_prompt(kind=None):
        cur = state.get("prompt")
        if kind and cur and cur.get("kind") != kind:
            return
        state["prompt"] = None
        p = prompt_palette(False)
        if p:
            p.isVisible = False

    def set_hidden_radio(cid, index):
        try:
            it = m._inputs.itemById(cid) if m._inputs else None
            if it is not None and 0 <= index < it.listItems.count:
                it.listItems.item(index).isSelected = True
        except Exception:
            pass

    def move_choice(value):
        if getattr(m, "_active_cmd", None) != "transform" or not m._pending:
            return
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
            try: m._app.activeViewport.refresh()
            except Exception: pass
        log("MOVE choice={} related={}".format(value, len(m._pending.get("related_bodies", []))))

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
                it.setManipulator(adsk.core.Point3D.create(*origin), adsk.core.Vector3D.create(*vec))
                it.isVisible = True; it.isEnabled = True
            except Exception:
                pass

    def scale_choice(value):
        if getattr(m, "_active_cmd", None) != "directional_scale" or not m._pending:
            return
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
            try: m._app.activeViewport.refresh()
            except Exception: pass
        log("SCALE choice={}".format(value))

    class PromptHTMLHandler(adsk.core.HTMLEventHandler):
        def notify(self, args):
            try:
                e = adsk.core.HTMLEventArgs.cast(args)
                if e.action == "ready":
                    if state.get("prompt"):
                        p = prompt_palette(False)
                        if p: p.sendInfoToHTML("prompt", json.dumps(state["prompt"]))
                    return
                if e.action != "choice":
                    return
                data = json.loads(e.data) if e.data else {}
                kind, value = data.get("kind"), data.get("value")
                if kind == "move_scope": move_choice(value)
                elif kind == "scale_scope": scale_choice(value)
            except Exception:
                log("popup event failed\n{}".format(m.traceback.format_exc()))

    class WatchInput(adsk.core.InputChangedEventHandler):
        def __init__(self, cmd_name):
            super().__init__(); self.cmd_name = cmd_name
        def notify(self, args):
            try:
                cid = args.input.id
                if self.cmd_name == "transform" and cid == "sel":
                    related = m._pending.get("related_bodies", []) if m._pending else []
                    if related:
                        show_prompt({
                            "kind": "move_scope",
                            "title": "Move connected parts?",
                            "message": "{} nearby part{} highlighted. Choose the movement scope.".format(len(related), " is" if len(related) == 1 else "s are"),
                            "selected": m._pending.get("move_scope", "only"),
                            "options": [
                                {"value": "only", "label": "Only this", "glyph": "●"},
                                {"value": "together", "label": "Together", "glyph": "◎"},
                            ]})
                    else:
                        hide_prompt("move_scope")
                elif self.cmd_name == "directional_scale" and cid == "dsb" and m._pending:
                    show_prompt({
                        "kind": "scale_scope",
                        "title": "Scale direction",
                        "message": "One-sided is the default. Choose the moving side, or scale symmetrically.",
                        "selected": m._pending.get("scale_side", "positive"),
                        "options": [
                            {"value": "positive", "label": "+ side", "glyph": "|→"},
                            {"value": "negative", "label": "- side", "glyph": "←|"},
                            {"value": "both", "label": "Both sides", "glyph": "↔"},
                        ]})
            except Exception:
                log("watch input failed\n{}".format(m.traceback.format_exc()))

    class HideOnDestroy(adsk.core.CommandEventHandler):
        def __init__(self, kind):
            super().__init__(); self.kind = kind
        def notify(self, args): hide_prompt(self.kind)

    class FuzzyCommandCreated(CurrentFuzzyCommandCreated):
        def notify(self, args):
            super().notify(args)
            try:
                inputs = args.command.commandInputs
                if self.cmd == "transform":
                    for cid in ("moveRelInfo", "moveScope"):
                        it = inputs.itemById(cid)
                        if it is not None: it.isVisible = False
                    h = WatchInput("transform"); args.command.inputChanged.add(h); m._handlers.append(h)
                    d = HideOnDestroy("move_scope"); args.command.destroy.add(d); m._handlers.append(d)
                elif self.cmd == "directional_scale":
                    it = inputs.itemById("dsSide")
                    if it is not None: it.isVisible = False
                    h = WatchInput("directional_scale"); args.command.inputChanged.add(h); m._handlers.append(h)
                    d = HideOnDestroy("scale_scope"); args.command.destroy.add(d); m._handlers.append(d)
            except Exception:
                log("command setup failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyCommandCreated = FuzzyCommandCreated

    def run(context):
        result = old_run(context)
        log("CUSTOM POPUP READY: Move/Scale choices are FuzzyCAD UI, not Fusion controls")
        return result

    def stop(context):
        try:
            p = prompt_palette(False)
            if p: p.deleteMe()
        except Exception:
            pass
        return old_stop(context)

    m.run = run
    m.stop = stop
