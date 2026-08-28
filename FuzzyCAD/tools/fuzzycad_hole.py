"""FuzzyCAD Hole tool (position + size Need Inputs).

A Hole decision owns four independent uncertain parameters on the selected planar
face: two face-local position offsets (U/V), diameter, and depth. The hole starts
at the face reference center, but that center is only a seed -- collaborators can
move it anywhere in the face plane before Accept.

Position is stored in a face-local basis instead of world X/Y/Z so the same
interaction works on arbitrarily oriented planar faces. The hole is still cut
with a temporary cylinder + boolean Cut because native HoleFeatures/construction
geometry are not supported reliably in imported/direct-modeling designs.
"""

import math


def install(m):
    adsk = m.adsk

    if "hole" not in m.COMMANDS:
        m.COMMANDS = tuple(m.COMMANDS) + ("hole",)
    m.CMD_ID["hole"] = "FuzzyCAD_Hole"
    m.CMD_LABEL["hole"] = "Hole"
    m.CMD_FILTER["hole"] = "PlanarFaces"
    m.CMD_HINT["hole"] = (
        "Select a planar face; position, diameter, and depth remain Need Input."
    )
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
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD HOLE] " + msg)
        except Exception:
            pass

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

    def mark_basis(mark):
        geom = m._geom.get(mark.get("id"), {}) or {}
        n = normalize(geom.get("normal", [0, 0, 1]))
        u = geom.get("basis_u")
        v = geom.get("basis_v")
        if not (isinstance(u, (list, tuple)) and len(u) == 3 and
                isinstance(v, (list, tuple)) and len(v) == 3):
            u, v = in_plane_basis(n)
        else:
            u, v = normalize(u), normalize(v)
        return n, u, v

    def hole_center(mark):
        _n, u, v = mark_basis(mark)
        base = list(mark.get("base_anchor") or mark.get("anchor") or [0.0, 0.0, 0.0])
        du = float(mark.get("offset_u", 0.0) or 0.0)
        dv = float(mark.get("offset_v", 0.0) or 0.0)
        return [base[i] + u[i] * du + v[i] * dv for i in range(3)]

    def update_anchor(mark):
        if mark is None:
            return
        mark["anchor"] = hole_center(mark)

    # Expose these pure helpers so the generic reopen module can use the exact
    # same face-local semantics without re-defining how Hole position is encoded.
    m._hole_basis = mark_basis
    m._hole_center = hole_center
    m._hole_update_anchor = update_anchor

    def build_pending(cmd, ent):
        if cmd == "hole":
            if not isinstance(ent, adsk.fusion.BRepFace):
                return None
            center, size = m._bbox_center_size(ent)
            nrm = m._face_normal(ent)
            u, v = in_plane_basis(nrm)
            return {
                "geom": {
                    "loops": m._sample_edges(ent.edges),
                    "normal": nrm,
                    "basis_u": list(u),
                    "basis_v": list(v),
                    "base_anchor": list(center),
                },
                "anchor": center,
                "size": size,
                "normal": nrm,
                "basis_u": list(u),
                "basis_v": list(v),
                "entity": ent,
                "body": m._entity_body(ent),
            }
        return old_build_pending(cmd, ent)

    m._build_pending = build_pending

    def category_raw(cat):
        if cat == "hole":
            return {
                "offset_u": m._val("hu"),
                "offset_v": m._val("hv"),
                "diameter": m._val("hd"),
                "depth": m._val("hp"),
            }
        return old_category_raw(cat)

    m._category_raw = category_raw

    def is_default(cat, op):
        if cat == "hole":
            return abs(op.get("diameter", 0.0)) < 1e-9
        return old_is_default(cat, op)

    m._is_default = is_default

    def fields(mark):
        if mark.get("tool") == "hole":
            return [
                {"key": "hu", "label": "Position U",
                 "value": round(float(mark.get("offset_u", 0.0)) * 10, 2), "unit": "mm"},
                {"key": "hv", "label": "Position V",
                 "value": round(float(mark.get("offset_v", 0.0)) * 10, 2), "unit": "mm"},
                {"key": "hd", "label": "Diameter",
                 "value": round(float(mark.get("diameter", 0.0)) * 10, 2), "unit": "mm"},
                {"key": "hp", "label": "Depth",
                 "value": round(float(mark.get("depth", 0.0)) * 10, 2), "unit": "mm"},
            ]
        return old_fields(mark)

    m._fields = fields

    def apply_edit(mark, key, value):
        if mark.get("tool") == "hole":
            try:
                raw = float(value) / 10.0
            except Exception:
                return
            if key == "hu":
                mark["offset_u"] = raw
                update_anchor(mark)
            elif key == "hv":
                mark["offset_v"] = raw
                update_anchor(mark)
            elif key == "hd":
                mark["diameter"] = max(0.01, raw)
            elif key == "hp":
                mark["depth"] = max(0.01, raw)
            return
        return old_apply_edit(mark, key, value)

    m._apply_edit = apply_edit

    def summary(mark):
        if mark.get("tool") == "hole":
            return "hole U{:+g} V{:+g}, ⌀{:g} × {:g} mm".format(
                float(mark.get("offset_u", 0.0)) * 10,
                float(mark.get("offset_v", 0.0)) * 10,
                float(mark.get("diameter", 0.0)) * 10,
                float(mark.get("depth", 0.0)) * 10,
            )
        return old_summary(mark)

    m._summary = summary

    def circle_pts(center, u, v, r, seg=40):
        pts = []
        for k in range(seg + 1):
            a = 2.0 * math.pi * k / seg
            cu, sv = math.cos(a) * r, math.sin(a) * r
            pts.append((center[0] + u[0] * cu + v[0] * sv,
                        center[1] + u[1] * cu + v[1] * sv,
                        center[2] + u[2] * cu + v[2] * sv))
        return pts

    CUT_RGB = (206, 66, 52)

    def draw_hole(group, mark, rgb, amp):
        n, u, v = mark_basis(mark)
        c = hole_center(mark)
        r = max(float(mark.get("diameter", 0.0)) / 2.0, 1e-4)
        depth = max(float(mark.get("depth", 0.0)), 1e-4)

        try:
            eps = max(depth * 0.03, 0.01)
            p_top = adsk.core.Point3D.create(
                c[0] + n[0] * eps, c[1] + n[1] * eps, c[2] + n[2] * eps)
            p_bot = adsk.core.Point3D.create(
                c[0] - n[0] * depth, c[1] - n[1] * depth, c[2] - n[2] * depth)
            cyl = adsk.fusion.TemporaryBRepManager.get().createCylinderOrCone(
                p_top, r, p_bot, r)
            if cyl is not None:
                cgb = group.addBRepBody(cyl)
                cgb.color = m._solid(CUT_RGB)
                cgb.setOpacity(0.40, True)
        except Exception:
            pass

        c_bot = [c[0] - n[0] * depth, c[1] - n[1] * depth, c[2] - n[2] * depth]
        top = circle_pts(c, u, v, r)
        bot = circle_pts(c_bot, u, v, r)
        m._sketchy(group, top, rgb, amp, mark["id"] * 41, weight=2, strokes=2)
        m._sketchy(group, bot, rgb, amp, mark["id"] * 43, weight=1, strokes=2)
        for k in range(0, len(top) - 1, max(1, len(top) // 6)):
            m._sketchy(group, [top[k], bot[k]], rgb, amp,
                       mark["id"] * 47 + k, weight=1, strokes=1)

        # Position uncertainty is visible while Editing as two short face-local
        # construction axes. Orange communicates locus/control, not proposal mass.
        try:
            span = max(float(mark.get("size", 1.0)) * 0.16, r * 1.4, 0.15)
            pu0 = tuple(c[i] - u[i] * span for i in range(3))
            pu1 = tuple(c[i] + u[i] * span for i in range(3))
            pv0 = tuple(c[i] - v[i] * span for i in range(3))
            pv1 = tuple(c[i] + v[i] * span for i in range(3))
            orange = (225, 126, 38)
            m._sketchy(group, [pu0, pu1], orange, 0.0,
                       mark["id"] * 59 + 1, weight=2, strokes=1)
            m._sketchy(group, [pv0, pv1], orange, 0.0,
                       mark["id"] * 59 + 2, weight=2, strokes=1)
        except Exception:
            pass

    m._DRAW["hole"] = draw_hole

    def seed_hole():
        size = m._pending.get("size", 3.0) if m._pending else 3.0
        d_dia = max(0.3, min(size * 0.3, 2.0))
        d_dep = max(0.3, min(size * 0.5, 3.0))
        for cid, val in (("hu", 0.0), ("hv", 0.0), ("hd", d_dia), ("hp", d_dep)):
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
                mk["offset_u"] = 0.0
                mk["offset_v"] = 0.0
                mk["diameter"] = d_dia
                mk["depth"] = d_dep
                update_anchor(mk)
            return

        mid = m._next_id
        m._next_id = mid + 1
        mark = m._make_mark("hole", {
            "offset_u": 0.0,
            "offset_v": 0.0,
            "diameter": d_dia,
            "depth": d_dep,
            "base_anchor": list(m._pending.get("anchor") or [0.0, 0.0, 0.0]),
        })
        mark["id"] = mid
        m._geom[mid] = m._pending["geom"]
        m._entity[mid] = m._pending["entity"]
        m._body[mid] = m._pending["body"]
        update_anchor(mark)
        m._marks.append(mark)
        m._live["hole"] = mid
        m._send_state()

    def place_param_manipulators():
        if not m._pending or m._inputs is None:
            return
        origin_xyz = list(m._pending["anchor"])
        origin = adsk.core.Point3D.create(*origin_xyz)
        n = normalize(m._pending.get("normal", [0, 0, 1]))
        u = normalize(m._pending.get("basis_u") or in_plane_basis(n)[0])
        v = normalize(m._pending.get("basis_v") or in_plane_basis(n)[1])
        try:
            for cid, direction in (("hu", u), ("hv", v)):
                it = m._inputs.itemById(cid)
                if it is not None:
                    it.setManipulator(origin, adsk.core.Vector3D.create(*direction))
                    it.isVisible = True
                    it.isEnabled = True

            hd = m._inputs.itemById("hd")
            if hd is not None:
                hd.setManipulator(origin, adsk.core.Vector3D.create(*u))
                hd.isVisible = True
                hd.isEnabled = True
            hp = m._inputs.itemById("hp")
            if hp is not None:
                hp.setManipulator(origin, adsk.core.Vector3D.create(-n[0], -n[1], -n[2]))
                hp.isVisible = True
                hp.isEnabled = True
        except Exception:
            log("manipulator placement failed\n{}".format(m.traceback.format_exc()))

    class FuzzyCommandCreated(CurrentCommandCreated):
        def notify(self, args):
            super().notify(args)
            if self.cmd != "hole":
                return
            try:
                inputs = args.command.commandInputs
                for cid, label in (("hu", "Position U"), ("hv", "Position V"),
                                   ("hd", "Diameter"), ("hp", "Depth")):
                    it = inputs.addDistanceValueCommandInput(
                        cid, label, adsk.core.ValueInput.createByReal(0.0))
                    it.isVisible = False
                    it.isEnabled = False
            except Exception:
                log("input setup failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyCommandCreated = FuzzyCommandCreated

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

                if cid in ("hu", "hv", "hd", "hp"):
                    mid = m._live.get("hole")
                    mk = m._find(mid) if mid is not None else None
                    if mk is not None:
                        mk["offset_u"] = float(m._val("hu"))
                        mk["offset_v"] = float(m._val("hv"))
                        mk["diameter"] = max(float(m._val("hd")), 1e-4)
                        mk["depth"] = max(float(m._val("hp")), 1e-4)
                        update_anchor(mk)
                        m._clear(m.GROUP_PREVIEW)
                        m._draw_one(m._group(m.GROUP_PREVIEW), mk)
                        m._refresh_ghost()
                        (getattr(m, "_send_state_throttled", None) or m._send_state)()
            except Exception:
                log("hole input failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyInputChanged = FuzzyInputChanged

    def accept_hole(mark):
        ent = m._entity.get(mark["id"])
        if ent is None:
            return False
        try:
            comp = ent.body.parentComponent
            target = ent.body
            n, _u, _v = mark_basis(mark)
            c = hole_center(mark)
            r = max(float(mark.get("diameter", 0.0)) / 2.0, 1e-4)
            depth = max(float(mark.get("depth", 0.0)), 1e-4)
            eps = max(depth * 0.05, 0.02)
            p_top = adsk.core.Point3D.create(
                c[0] + n[0] * eps, c[1] + n[1] * eps, c[2] + n[2] * eps)
            p_bot = adsk.core.Point3D.create(
                c[0] - n[0] * depth, c[1] - n[1] * depth, c[2] - n[2] * depth)
            temp = adsk.fusion.TemporaryBRepManager.get()
            cyl = temp.createCylinderOrCone(p_top, r, p_bot, r)
            if cyl is None:
                return False
            base_feats = comp.features.baseFeatures
            bf = base_feats.add()
            bf.startEdit()
            comp.bRepBodies.add(cyl, bf)
            bf.finishEdit()
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

    log("HOLE READY: face-local U/V position + diameter + depth")
