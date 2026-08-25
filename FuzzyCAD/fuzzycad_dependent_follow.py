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

    def detect_dependents(primary, exclude=None):
        """Bodies that touch the marked body (bounding-box contact) -- the same
        proven proximity signal Move scope uses. Loose on purpose: the user
        confirms the set, so a false positive just gets left unchecked.

        `exclude` is a set of tokens already decided before the move (the pre-move
        Only/Together set), so the same parts are never asked about twice."""
        if primary is None:
            return []
        exclude = exclude or set()
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
                if tok is None or tok == ptok or tok in seen or tok in exclude:
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
                "Found {} part{} built on this (highlighted).\n\n"
                "Carry {} along with this change so {} stay attached?".format(
                    count, "" if count == 1 else "s",
                    "it" if count == 1 else "them",
                    "it does" if count == 1 else "they do"),
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

    def accept(mark):
        tool = mark.get("tool")
        # Rigid co-motion tools: Move, Rotate (world axes) and Axis Rotate.
        # Pre-selected move-together is already handled by fuzzycad_move_scope,
        # so don't double-ask for that one.
        eligible = (tool == "axis_rotate"
                    or (tool in ("move", "rotate") and mark.get("move_scope") != "together"))
        if eligible:
            primary = m._body.get(mark["id"])
            # Parts already decided before the move (the pre-move Only/Together
            # set) are not asked about again here.
            exclude = set()
            for b in mark.get("related_bodies") or []:
                t = body_token(b)
                if t:
                    exclude.add(t)
            deps = []
            try:
                deps = detect_dependents(primary, exclude)
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

    m._accept = accept
    log("DEPENDENT FOLLOW READY (rigid move/rotate)")
