"""Let work built on a fuzzy part follow it when the part is resolved.

The core finding: in async CAD, uncertainty is transitive. If you build a chin
guard on a headrest whose angle is still an open question, accepting the headrest
angle must carry the chin guard along -- otherwise the "final" model is silently
wrong (new fake certainty).

This module handles the rigid co-motion case (Move / Rotate). When such a mark is
accepted, it finds the bodies that are built on the marked body -- detected by a
shared, coincident planar face (the anchor face the downstream sits on) -- shows
them highlighted, and asks once whether to carry them along. If yes, the SAME
rigid transform (m._op_matrix) is applied to the marked body and the confirmed
dependants together, so their relative geometry is preserved exactly.

Scale / Extrude are NOT rigid: different faces move by different amounts, so those
follow a per-anchor-face displacement and are handled separately (next step). Here
we only do the rigid Move/Rotate case; everything else falls through unchanged.

Only bodies the user confirms are moved -- the system makes the coupling visible
and asks; the decision stays with the user.
"""

import math


def install(m):
    adsk = m.adsk
    old_accept = m._accept

    DEP_GID = "FuzzyCAD_FollowHighlight"
    FOLLOW_RGB = (225, 126, 38)

    def log(msg):
        # Always write to the app log (visible in Text Commands) so detection can
        # be diagnosed even in the non-dev build where _debug is a no-op.
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD FOLLOW] " + msg)
        except Exception:
            pass

    def body_token(b):
        try:
            return b.entityToken
        except Exception:
            return None

    def bbox_gap(a, b):
        amn, amx = a.minPoint, a.maxPoint
        bmn, bmx = b.minPoint, b.maxPoint
        dx = max(0.0, bmn.x - amx.x, amn.x - bmx.x)
        dy = max(0.0, bmn.y - amx.y, amn.y - bmx.y)
        dz = max(0.0, bmn.z - amx.z, amn.z - bmx.z)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def all_bodies(design):
        """Every solid body in the design: root plus all occurrence proxies, so a
        shape built in a different component from the marked part is still seen."""
        out = []
        try:
            root = design.rootComponent
            for i in range(root.bRepBodies.count):
                out.append(root.bRepBodies.item(i))
            occs = root.allOccurrences
            for i in range(occs.count):
                try:
                    bs = occs.item(i).bRepBodies
                    for j in range(bs.count):
                        out.append(bs.item(j))
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def detect_dependents(primary):
        """Bodies that touch the marked body (bounding-box contact) -- the same
        proven proximity signal Move scope uses. Loose on purpose: the user
        confirms the set, so a false positive just gets left unchecked."""
        if primary is None:
            return []
        design = m._design()
        if design is None:
            return []
        try:
            _, size = m._bbox_center_size(primary)
            pbb = primary.boundingBox
        except Exception:
            return []
        tol = max(0.05, min(float(size) * 0.02, 0.20))   # 0.5mm .. 2mm contact tol
        ptok = body_token(primary)
        out = []
        seen = set()
        for b in all_bodies(design):
            try:
                tok = body_token(b)
                if tok is None or tok == ptok or tok in seen:
                    continue
                seen.add(tok)
                if hasattr(b, "isVisible") and not b.isVisible:
                    continue
                try:
                    if m._body_locked(b):     # has its own open question — leave it
                        continue
                except Exception:
                    pass
                if bbox_gap(pbb, b.boundingBox) <= tol:
                    out.append(b)
            except Exception:
                continue
        log("DETECT dependents primary={} found={} tol_mm={:.2f}".format(
            getattr(primary, "name", "body"), len(out), tol * 10.0))
        return out[:12]

    def highlight(bodies, on):
        try:
            m._clear(DEP_GID)
        except Exception:
            return
        if not on:
            try:
                m._app.activeViewport.refresh()
            except Exception:
                pass
            return
        grp = m._group(DEP_GID)
        if grp is None:
            return
        for b in bodies:
            try:
                cg = grp.addBRepBody(b)
                cg.color = m._solid(FOLLOW_RGB)
                cg.setOpacity(0.45, True)
            except Exception:
                continue
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def confirm(count):
        try:
            res = m._ui.messageBox(
                "{} part{} built on this were found.\n\n"
                "Carry them along with this change so they stay attached?".format(
                    count, " is" if count == 1 else "s are"),
                "FuzzyCAD — dependent parts",
                adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                adsk.core.MessageBoxIconTypes.QuestionIconType)
            return res == adsk.core.DialogResults.DialogYes
        except Exception:
            return False

    def rigid_matrix(mark):
        """The rigid transform for a co-motion mark, in world space."""
        if mark.get("tool") == "axis_rotate":
            g = m._geom.get(mark["id"], {})
            origin = g.get("axis_origin") or mark.get("axis_origin") or [0.0, 0.0, 0.0]
            direction = g.get("axis_dir") or mark.get("axis_dir") or [0.0, 0.0, 1.0]
            mat = adsk.core.Matrix3D.create()
            mat.setToRotation(
                math.radians(float(mark.get("angle", 0.0))),
                adsk.core.Vector3D.create(*direction),
                adsk.core.Point3D.create(*origin))
            return mat
        return m._op_matrix(mark)

    def apply_together(matrix, primary, deps):
        """Apply the same rigid transform to the marked body and the dependants.
        Bodies are grouped by their owning component (a MoveFeature is scoped to
        one component), so a shape built in a different component still moves."""
        try:
            groups = []      # list of (component, [bodies])
            added = set()

            def enroll(b):
                tok = body_token(b)
                if tok is None or tok in added:
                    return
                added.add(tok)
                try:
                    comp = b.parentComponent
                except Exception:
                    return
                for g in groups:
                    if g[0] == comp:
                        g[1].append(b); return
                groups.append((comp, [b]))

            enroll(primary)
            for b in deps:
                enroll(b)

            moved = 0
            for comp, bodies in groups:
                coll = adsk.core.ObjectCollection.create()
                for b in bodies:
                    coll.add(b)
                comp.features.moveFeatures.add(
                    comp.features.moveFeatures.createInput(coll, matrix))
                moved += coll.count
            log("APPLIED move/rotate to {} bodies across {} component(s)".format(
                moved, len(groups)))
            return True
        except Exception:
            m._ui.messageBox("FuzzyCAD couldn't carry the dependent parts:\n{}".format(
                m.traceback.format_exc()))
            return False

    # ---- non-rigid follow (Scale / Extrude): per-body displacement ---------
    def normalize(v):
        n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
        return [v[0] / n, v[1] / n, v[2] / n]

    def resolve_body(design, tok):
        if design is None or not tok:
            return None
        try:
            ents = design.findEntityByToken(tok)
        except Exception:
            return None
        try:
            for e in ents:
                if isinstance(e, adsk.fusion.BRepBody):
                    return e
        except Exception:
            pass
        return None

    def displacement(mark, p):
        """How far a dependant at point p should translate to stay attached.

        Scale moves each point away from the centre in proportion to distance
        ((f-1)(p-c)); a directional scale only along its axis; an extrude pushes
        the extruded face by d along its normal."""
        tool = mark.get("tool")
        if tool == "scale":
            c = mark.get("anchor", [0.0, 0.0, 0.0])
            f = float(mark.get("factor", 1.0))
            return [(f - 1.0) * (p[i] - c[i]) for i in range(3)]
        if tool == "scale_axis":
            base = mark.get("base_anchor") or mark.get("anchor") or [0.0, 0.0, 0.0]
            f = float(mark.get("factor", 1.0))
            idx = {"X": 0, "Y": 1, "Z": 2}.get(mark.get("axis", "X"), 0)
            d = [0.0, 0.0, 0.0]
            d[idx] = (f - 1.0) * (p[idx] - base[idx])
            return d
        if tool == "extrude":
            n = normalize(m._geom.get(mark["id"], {}).get("normal", [0.0, 0.0, 1.0]))
            amt = float(mark.get("amount", 0.0))
            return [n[i] * amt for i in range(3)]
        return [0.0, 0.0, 0.0]

    def translate_body(body, disp):
        comp = body.parentComponent
        coll = adsk.core.ObjectCollection.create()
        coll.add(body)
        mtx = adsk.core.Matrix3D.create()
        mtx.translation = adsk.core.Vector3D.create(disp[0], disp[1], disp[2])
        comp.features.moveFeatures.add(
            comp.features.moveFeatures.createInput(coll, mtx))

    def detect_flex_deps(mark, primary):
        """Dependants for a Scale/Extrude. For Extrude, only bodies touching the
        extruded face itself follow (parts on other faces don't move)."""
        tool = mark.get("tool")
        design = m._design()
        if design is None or primary is None:
            return []
        try:
            _, size = m._bbox_center_size(primary)
        except Exception:
            size = 3.0
        tol = max(0.05, min(float(size) * 0.02, 0.20))
        if tool == "extrude":
            ent = m._entity.get(mark["id"])
            try:
                target = ent.boundingBox
            except Exception:
                return []
        else:
            try:
                target = primary.boundingBox
            except Exception:
                return []
        ptok = body_token(primary)
        out = []
        seen = set()
        for b in all_bodies(design):
            try:
                tok = body_token(b)
                if not tok or tok == ptok or tok in seen:
                    continue
                seen.add(tok)
                if hasattr(b, "isVisible") and not b.isVisible:
                    continue
                try:
                    if m._body_locked(b):
                        continue
                except Exception:
                    pass
                if bbox_gap(target, b.boundingBox) <= tol:
                    out.append(b)
            except Exception:
                continue
        log("DETECT flex deps tool={} found={}".format(tool, len(out)))
        return out[:12]

    RIGID_TOOLS = ("move", "rotate")
    FLEX_TOOLS = ("scale", "scale_axis", "extrude")

    def accept(mark):
        tool = mark.get("tool")

        # Rigid co-motion: Move / Rotate (world axes) / Axis Rotate -> the same
        # transform to every confirmed dependant.
        if tool == "axis_rotate" or (tool in RIGID_TOOLS and mark.get("move_scope") != "together"):
            primary = m._body.get(mark["id"])
            deps = []
            try:
                deps = detect_dependents(primary)
            except Exception:
                deps = []
            if deps:
                highlight(deps, True)
                take = confirm(len(deps))
                highlight(deps, False)
                if take:
                    try:
                        primary.opacity = 1.0
                    except Exception:
                        pass
                    return apply_together(rigid_matrix(mark), primary, deps)
            return old_accept(mark)

        # Non-rigid: Scale / Extrude -> each dependant follows its own local
        # displacement (position follows; the primary is scaled/extruded as usual).
        if tool in FLEX_TOOLS:
            primary = m._body.get(mark["id"])
            deps = []
            try:
                deps = detect_flex_deps(mark, primary)
            except Exception:
                deps = []
            if deps:
                highlight(deps, True)
                take = confirm(len(deps))
                highlight(deps, False)
                if take:
                    design = m._design()
                    # Capture each dependant (ref + token) and its move from the
                    # pre-op centroid.
                    plan = []
                    for b in deps:
                        try:
                            c = m._bbox_center_size(b)[0]
                            disp = displacement(mark, c)
                            plan.append((b, body_token(b), disp))
                            log("FLEX plan tok={} disp=({:.3f}, {:.3f}, {:.3f})".format(
                                body_token(b), disp[0], disp[1], disp[2]))
                        except Exception:
                            log("FLEX plan failed\n{}".format(m.traceback.format_exc()))
                    ok = old_accept(mark)      # scale / extrude the primary
                    moved = 0
                    if ok:
                        for body, tok, disp in plan:
                            if all(abs(x) <= 1e-9 for x in disp):
                                continue
                            target = body
                            try:
                                if not target.isValid:
                                    target = None
                            except Exception:
                                target = None
                            if target is None:
                                target = resolve_body(design, tok)
                            if target is None:
                                log("FLEX skip: body lost tok={}".format(tok))
                                continue
                            try:
                                translate_body(target, disp)
                                moved += 1
                            except Exception:
                                log("FLEX translate failed\n{}".format(m.traceback.format_exc()))
                        log("FLEX moved {} of {} (tool={})".format(moved, len(plan), tool))
                    return ok

        return old_accept(mark)

    m._accept = accept
    log("DEPENDENT FOLLOW READY (move/rotate rigid + scale/extrude per-body)")
