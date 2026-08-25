"""FuzzyCAD Hole tool (open-parameter Need Inputs).

Select a planar face; a simple round hole is centred on it and drilled along the
face normal. BOTH the diameter and the depth are left as explicit Need Inputs for
a collaborator to resolve -- the paper's "open parameter range" idea applied to a
hole.

The hole is cut with a temporary cylinder + boolean Cut rather than the native
Hole feature, because construction/sketch geometry (and HoleFeatures) raise
"Environment is not supported" in imported / direct-modeling designs, while
temporary-body combine on real bodies works there.

Standalone module: it extends the command tables and wraps the relevant legacy
functions, so the legacy core is left untouched.
"""

import math


def install(m):
    adsk = m.adsk

    # ---- register the Hole command ----------------------------------------
    if "hole" not in m.COMMANDS:
        m.COMMANDS = tuple(m.COMMANDS) + ("hole",)
    m.CMD_ID["hole"] = "FuzzyCAD_Hole"
    m.CMD_LABEL["hole"] = "Hole"
    m.CMD_FILTER["hole"] = "PlanarFaces"
    m.CMD_HINT["hole"] = "Select a planar face; a hole is centred on it. Diameter and depth are Need Input."
    m.CMD_CATS["hole"] = ("hole",)

    old_build_pending = m._build_pending
    old_category_raw = m._category_raw
    old_is_default = m._is_default
    old_fields = m._fields
    old_apply_edit = m._apply_edit
    old_summary = m._summary
    old_accept = m._accept
    CurrentInputChanged = m.FuzzyInputChanged
    CurrentCommandCreated = m.FuzzyCommandCreated

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD HOLE] " + msg)
        except Exception:
            pass

    # ---- vector helpers ---------------------------------------------------
    def normalize(v):
        n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
        return [v[0] / n, v[1] / n, v[2] / n]

    def cross(a, b):
        return [a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0]]

    def in_plane_basis(n):
        n = normalize(n)
        ref = [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0]
        u = normalize(cross(n, ref))
        v = normalize(cross(n, u))
        return u, v

    # ---- pending (a planar face, like extrude) ----------------------------
    def build_pending(cmd, ent):
        if cmd == "hole":
            if not isinstance(ent, adsk.fusion.BRepFace):
                return None
            center, size = m._bbox_center_size(ent)
            nrm = m._face_normal(ent)
            return {"geom": {"loops": m._sample_edges(ent.edges), "normal": nrm},
                    "anchor": center, "size": size, "normal": nrm,
                    "entity": ent, "body": m._entity_body(ent)}
        return old_build_pending(cmd, ent)

    m._build_pending = build_pending

    # ---- parameter reading / defaults -------------------------------------
    def category_raw(cat):
        if cat == "hole":
            return {"diameter": m._val("hd"), "depth": m._val("hp")}
        return old_category_raw(cat)

    m._category_raw = category_raw

    def is_default(cat, op):
        if cat == "hole":
            return abs(op.get("diameter", 0.0)) < 1e-9
        return old_is_default(cat, op)

    m._is_default = is_default

    # ---- card fields / edits / summary ------------------------------------
    def fields(mark):
        if mark["tool"] == "hole":
            return [{"key": "hd", "label": "Diameter", "value": round(mark.get("diameter", 0.0) * 10, 2),
                     "unit": "mm"},
                    {"key": "hp", "label": "Depth", "value": round(mark.get("depth", 0.0) * 10, 2),
                     "unit": "mm"}]
        return old_fields(mark)

    m._fields = fields

    def apply_edit(mark, key, value):
        if mark["tool"] == "hole":
            try:
                v = max(0.01, float(value) / 10.0)
            except Exception:
                return
            if key == "hd":
                mark["diameter"] = v
            elif key == "hp":
                mark["depth"] = v
            return
        return old_apply_edit(mark, key, value)

    m._apply_edit = apply_edit

    def summary(mark):
        if mark["tool"] == "hole":
            return "hole ⌀{:g} × {:g} mm".format(
                mark.get("diameter", 0.0) * 10, mark.get("depth", 0.0) * 10)
        return old_summary(mark)

    m._summary = summary

    # ---- 3D ghost ---------------------------------------------------------
    def circle_pts(center, u, v, r, seg=40):
        pts = []
        for k in range(seg + 1):
            a = 2.0 * math.pi * k / seg
            cu, sv = math.cos(a) * r, math.sin(a) * r
            pts.append((center[0] + u[0] * cu + v[0] * sv,
                        center[1] + u[1] * cu + v[1] * sv,
                        center[2] + u[2] * cu + v[2] * sv))
        return pts

    # "To be removed" tint for the proposed cavity — reads as a real hole while
    # the host body is ghosted, and updates every frame as diameter/depth change.
    CUT_RGB = (206, 66, 52)

    def draw_hole(group, mark, rgb, amp):
        g = m._geom.get(mark["id"], {})
        n = normalize(g.get("normal", [0, 0, 1]))
        u, v = in_plane_basis(n)
        c = mark.get("anchor", [0, 0, 0])
        r = max(mark.get("diameter", 0.0) / 2.0, 1e-4)
        depth = max(mark.get("depth", 0.0), 1e-4)

        # 1) Solid semi-transparent cylinder = the exact hole volume. A cheap
        #    transient BRep (not a feature build), rebuilt each redraw so the
        #    shape follows the manipulator/card live, like Fusion's own preview.
        try:
            eps = max(depth * 0.03, 0.01)
            p_top = adsk.core.Point3D.create(c[0] + n[0] * eps, c[1] + n[1] * eps, c[2] + n[2] * eps)
            p_bot = adsk.core.Point3D.create(c[0] - n[0] * depth, c[1] - n[1] * depth, c[2] - n[2] * depth)
            cyl = adsk.fusion.TemporaryBRepManager.get().createCylinderOrCone(p_top, r, p_bot, r)
            if cyl is not None:
                cgb = group.addBRepBody(cyl)
                cgb.color = m._solid(CUT_RGB)
                cgb.setOpacity(0.40, True)
        except Exception:
            pass

        # 2) Sketchy rims + a few wall strokes keep the hand-drawn "this size is
        #    still uncertain" language on top of the solid preview.
        c_bot = [c[0] - n[0] * depth, c[1] - n[1] * depth, c[2] - n[2] * depth]
        top = circle_pts(c, u, v, r)
        bot = circle_pts(c_bot, u, v, r)
        m._sketchy(group, top, rgb, amp, mark["id"] * 41, weight=2, strokes=2)
        m._sketchy(group, bot, rgb, amp, mark["id"] * 43, weight=1, strokes=2)
        for k in range(0, len(top) - 1, max(1, len(top) // 6)):
            m._sketchy(group, [top[k], bot[k]], rgb, amp, mark["id"] * 47 + k, weight=1, strokes=1)

    m._DRAW["hole"] = draw_hole

    # ---- seeding on selection ---------------------------------------------
    def seed_hole():
        size = m._pending.get("size", 3.0) if m._pending else 3.0
        d_dia = max(0.3, min(size * 0.3, 2.0))
        d_dep = max(0.3, min(size * 0.5, 3.0))
        for cid, val in (("hd", d_dia), ("hp", d_dep)):
            it = m._inputs.itemById(cid) if m._inputs else None
            if it is not None:
                try:
                    it.value = val
                except Exception:
                    pass
        mid = m._live.get("hole")
        if mid is not None:
            mk = m._find(mid)
            if mk is not None:
                mk["diameter"] = d_dia
                mk["depth"] = d_dep
            return
        mid = m._next_id
        m._next_id = mid + 1
        mark = m._make_mark("hole", {"diameter": d_dia, "depth": d_dep})
        mark["id"] = mid
        m._geom[mid] = m._pending["geom"]
        m._entity[mid] = m._pending["entity"]
        m._body[mid] = m._pending["body"]
        m._marks.append(mark)
        m._live["hole"] = mid
        m._send_state()

    def place_param_manipulators():
        if not m._pending or m._inputs is None:
            return
        origin = adsk.core.Point3D.create(*m._pending["anchor"])
        n = normalize(m._pending.get("normal", [0, 0, 1]))
        try:
            u, _ = in_plane_basis(n)
            hd = m._inputs.itemById("hd")
            if hd is not None:
                hd.setManipulator(origin, adsk.core.Vector3D.create(*u))
                hd.isVisible = True; hd.isEnabled = True
            hp = m._inputs.itemById("hp")
            if hp is not None:
                hp.setManipulator(origin, adsk.core.Vector3D.create(-n[0], -n[1], -n[2]))
                hp.isVisible = True; hp.isEnabled = True
        except Exception:
            log("manipulator placement failed\n{}".format(m.traceback.format_exc()))

    # ---- command inputs ---------------------------------------------------
    class FuzzyCommandCreated(CurrentCommandCreated):
        def notify(self, args):
            super().notify(args)
            if self.cmd != "hole":
                return
            try:
                inputs = args.command.commandInputs
                for cid, label in (("hd", "Diameter"), ("hp", "Depth")):
                    it = inputs.addDistanceValueCommandInput(
                        cid, label, adsk.core.ValueInput.createByReal(0.0))
                    it.isVisible = False; it.isEnabled = False
            except Exception:
                log("input setup failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyCommandCreated = FuzzyCommandCreated

    # ---- seed + draw on selection -----------------------------------------
    class FuzzyInputChanged(CurrentInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass
            super().notify(args)
            if getattr(m, "_active_cmd", None) != "hole" or not m._pending:
                return
            try:
                if cid == "sel":
                    seed_hole()
                    place_param_manipulators()
                    mid = m._live.get("hole")
                    if mid is not None:
                        m._clear(m.GROUP_PREVIEW)
                        m._draw_one(m._group(m.GROUP_PREVIEW), m._find(mid))
                        m._refresh_ghost()
                        m._app.activeViewport.refresh()
                    return

                if cid in ("hd", "hp"):
                    # Manipulator drag is delivered as inputChanged in this build,
                    # not executePreview — sync straight from the native distance
                    # inputs so the cavity follows the handle live. No explicit
                    # viewport refresh: that would release the manipulator mid-drag.
                    mid = m._live.get("hole")
                    mk = m._find(mid) if mid is not None else None
                    if mk is not None:
                        mk["diameter"] = max(m._val("hd"), 1e-4)
                        mk["depth"] = max(m._val("hp"), 1e-4)
                        m._clear(m.GROUP_PREVIEW)
                        m._draw_one(m._group(m.GROUP_PREVIEW), mk)
                        m._refresh_ghost()
                        m._send_state()
            except Exception:
                log("hole input failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyInputChanged = FuzzyInputChanged

    # ---- apply to real geometry -------------------------------------------
    def accept_hole(mark):
        ent = m._entity.get(mark["id"])
        if ent is None:
            return False
        try:
            comp = ent.body.parentComponent
            target = ent.body
            n = normalize(m._geom.get(mark["id"], {}).get("normal", [0, 0, 1]))
            c = mark.get("anchor", [0, 0, 0])
            r = max(float(mark.get("diameter", 0.0)) / 2.0, 1e-4)
            depth = max(float(mark.get("depth", 0.0)), 1e-4)
            eps = max(depth * 0.05, 0.02)
            p_top = adsk.core.Point3D.create(c[0] + n[0] * eps, c[1] + n[1] * eps, c[2] + n[2] * eps)
            p_bot = adsk.core.Point3D.create(c[0] - n[0] * depth, c[1] - n[1] * depth, c[2] - n[2] * depth)
            temp = adsk.fusion.TemporaryBRepManager.get()
            cyl = temp.createCylinderOrCone(p_top, r, p_bot, r)
            if cyl is None:
                return False
            base_feats = comp.features.baseFeatures
            bf = base_feats.add()
            bf.startEdit()
            comp.bRepBodies.add(cyl, bf)
            bf.finishEdit()
            # The body proxy returned during the edit is invalidated by
            # finishEdit ("ALL_TOOL_BODY_REFERENCE_LOST"). Re-fetch the base
            # feature's own result body for the combine.
            tools = adsk.core.ObjectCollection.create()
            try:
                for i in range(bf.bodies.count):
                    tools.add(bf.bodies.item(i))
            except Exception:
                pass
            if tools.count < 1:
                m._ui.messageBox("FuzzyCAD couldn't build the hole tool body.")
                return False
            combos = comp.features.combineFeatures
            ci = combos.createInput(target, tools)
            ci.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
            ci.isKeepToolBodies = False
            combos.add(ci)
            return True
        except Exception:
            m._ui.messageBox("FuzzyCAD couldn't drill the hole:\n{}".format(
                m.traceback.format_exc()))
            return False

    def accept(mark):
        if mark.get("tool") == "hole":
            return accept_hole(mark)
        return old_accept(mark)

    m._accept = accept

    log("HOLE READY")
