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
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
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

    def planar(face):
        """Return (point, unit-normal) for a planar face, else None."""
        try:
            geo = face.geometry
            if not isinstance(geo, adsk.core.Plane):
                return None
            o = geo.origin
            n = geo.normal.copy(); n.normalize()
            return (o, n)
        except Exception:
            return None

    def boxes_overlap(a, b, tol):
        return not (b.minPoint.x - a.maxPoint.x > tol or a.minPoint.x - b.maxPoint.x > tol or
                    b.minPoint.y - a.maxPoint.y > tol or a.minPoint.y - b.maxPoint.y > tol or
                    b.minPoint.z - a.maxPoint.z > tol or a.minPoint.z - b.maxPoint.z > tol)

    def faces_coincident(fa, fb, tol, ang=0.985):
        pa = planar(fa); pb = planar(fb)
        if pa is None or pb is None:
            return False
        (oa, na), (ob, nb) = pa, pb
        # normals parallel or anti-parallel
        if abs(na.x * nb.x + na.y * nb.y + na.z * nb.z) < ang:
            return False
        # the two planes are (nearly) the same plane: ob lies on A's plane
        d = abs((ob.x - oa.x) * na.x + (ob.y - oa.y) * na.y + (ob.z - oa.z) * na.z)
        if d > tol:
            return False
        try:
            return boxes_overlap(fa.boundingBox, fb.boundingBox, tol)
        except Exception:
            return True

    def is_dependent(primary, other, tol):
        """other is built on primary if they share a coincident planar face."""
        try:
            if bbox_gap(primary.boundingBox, other.boundingBox) > tol:
                return False
        except Exception:
            return False
        try:
            fa_all = primary.faces
            fb_all = other.faces
            for i in range(fa_all.count):
                fa = fa_all.item(i)
                if planar(fa) is None:
                    continue
                for j in range(fb_all.count):
                    if faces_coincident(fa, fb_all.item(j), tol):
                        return True
        except Exception:
            return False
        return False

    def detect_dependents(primary):
        if primary is None:
            return []
        try:
            comp = primary.parentComponent
            bodies = comp.bRepBodies
            _, size = m._bbox_center_size(primary)
        except Exception:
            return []
        tol = max(0.05, min(float(size) * 0.02, 0.20))   # 0.5mm .. 2mm contact tol
        ptok = body_token(primary)
        out = []
        for i in range(bodies.count):
            try:
                b = bodies.item(i)
                if body_token(b) == ptok:
                    continue
                if hasattr(b, "isVisible") and not b.isVisible:
                    continue
                try:
                    if m._body_locked(b):     # has its own open question — leave it
                        continue
                except Exception:
                    pass
                if is_dependent(primary, b, tol):
                    out.append(b)
            except Exception:
                continue
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

    def apply_together(mark, primary, deps):
        try:
            comp = primary.parentComponent
            coll = adsk.core.ObjectCollection.create()
            coll.add(primary)
            added = {body_token(primary)}
            for b in deps:
                tok = body_token(b)
                if tok in added:
                    continue
                try:
                    if b.parentComponent != comp:
                        continue
                except Exception:
                    continue
                coll.add(b)
                added.add(tok)
            comp.features.moveFeatures.add(
                comp.features.moveFeatures.createInput(coll, m._op_matrix(mark)))
            log("APPLIED move/rotate to {} bodies (1 marked + {} dependants)".format(
                coll.count, coll.count - 1))
            return True
        except Exception:
            m._ui.messageBox("FuzzyCAD couldn't carry the dependent parts:\n{}".format(
                m.traceback.format_exc()))
            return False

    def accept(mark):
        tool = mark.get("tool")
        # Only rigid co-motion here; pre-selected move-together is already handled
        # by fuzzycad_move_scope, so don't double-ask for it.
        if tool in ("move", "rotate") and mark.get("move_scope") != "together":
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
                    return apply_together(mark, primary, deps)
        return old_accept(mark)

    m._accept = accept
    log("DEPENDENT FOLLOW READY (rigid move/rotate)")
