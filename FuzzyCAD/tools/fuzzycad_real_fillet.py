"""Runtime patch for FuzzyCAD's Fusion 360 fillet workflow.

Keeps the existing add-in intact and replaces only the fillet path with:
- a kernel-generated, transient BRep candidate shown as CustomGraphics;
- adaptive candidate/fillet-edge outlines;
- a kernel-measured maximum radius wired to the distance manipulator;
- snap-back to the last valid radius if Fusion rejects an intermediate value.
"""

import math

FILLET_MIN_CM = 0.01          # 0.1 mm; Fusion API internal length unit is cm
SEARCH_STEPS = 8
SAMPLE_SPACING_CM = 0.32      # 3.2 mm, close to the FeatureScript visual density
SAMPLE_MIN = 5
SAMPLE_MAX = 160
CANDIDATE_OPACITY = 0.30
CANDIDATE_RGB = (190, 190, 186)
FILLET_RGB = (225, 126, 38)
WARN_RGB = (200, 44, 32)


def install(m):
    """Monkey-patch the already-loaded legacy module in place."""
    adsk = m.adsk

    old_build_pending = m._build_pending
    old_make_mark = m._make_mark
    old_seed_single = m._seed_single
    old_fields = m._fields
    old_apply_edit = m._apply_edit
    old_accept = m._accept
    old_draw_label = m._draw_label
    old_real_extrude_edges = m._real_extrude_edges

    def sample_edge(edge, n=None):
        pts = []
        try:
            if n is None:
                try:
                    count = int(math.ceil(max(float(edge.length), 1e-6) / SAMPLE_SPACING_CM))
                    n = max(SAMPLE_MIN, min(SAMPLE_MAX, count))
                except Exception:
                    n = 10
            n = max(2, int(n))
            ev = edge.geometry.evaluator
            ok, tmin, tmax = ev.getParameterExtents()
            if not ok:
                return pts
            for i in range(n + 1):
                t = tmin + (tmax - tmin) * i / n
                ok, p = ev.getPointAtParameter(t)
                if ok:
                    pts.append((p.x, p.y, p.z))
        except Exception:
            try:
                s = edge.startVertex.geometry
                e = edge.endVertex.geometry
                pts = [(s.x, s.y, s.z), (e.x, e.y, e.z)]
            except Exception:
                pass
        return pts

    def sample_edges(collection):
        out = []
        for i in range(collection.count):
            p = sample_edge(collection.item(i))
            if len(p) >= 2:
                out.append(p)
        return out

    def sample_created_face_edges(faces):
        out, seen = [], set()
        for i in range(faces.count):
            face = faces.item(i)
            for j in range(face.edges.count):
                edge = face.edges.item(j)
                try:
                    key = edge.entityToken
                except Exception:
                    key = "{}:{}".format(i, j)
                if key in seen:
                    continue
                seen.add(key)
                p = sample_edge(edge)
                if len(p) >= 2:
                    out.append(p)
        return out

    m._sample_edge = sample_edge
    m._sample_edges = sample_edges

    def build_pending(cmd, ent):
        pending = old_build_pending(cmd, ent)
        if cmd != "fillet" or pending is None:
            return pending
        try:
            token = ent.entityToken
        except Exception:
            token = None
        try:
            _, body_size = m._bbox_center_size(ent.body)
        except Exception:
            body_size = pending.get("size", 1.0)
        pending["edge_token"] = token
        pending["body_size"] = body_size
        pending["geom"]["edge_token"] = token
        pending["geom"]["body_size"] = body_size
        pending["geom"]["max_radius"] = None
        pending["geom"]["candidate_body"] = None
        pending["geom"]["candidate_radius"] = None
        pending["geom"]["candidate_edges"] = []
        pending["geom"]["fillet_edges"] = []
        return pending

    m._build_pending = build_pending

    def make_mark(tool, op):
        mark = old_make_mark(tool, op)
        if tool == "fillet":
            mark["last_valid_amount"] = max(op.get("amount", FILLET_MIN_CM), FILLET_MIN_CM)
        return mark

    m._make_mark = make_mark

    def seed_single(cat, amount):
        old_seed_single(cat, amount)
        if cat == "fillet":
            mid = m._live.get(cat)
            mark = m._find(mid) if mid is not None else None
            if mark:
                mark["last_valid_amount"] = max(amount, FILLET_MIN_CM)

    m._seed_single = seed_single

    def resolve_edge(token, fallback=None):
        try:
            if fallback is not None and fallback.isValid:
                return fallback
        except Exception:
            pass
        design = m._design()
        if design is None or not token:
            return None
        try:
            for ent in design.findEntityByToken(token):
                if isinstance(ent, adsk.fusion.BRepEdge):
                    return ent
        except Exception:
            pass
        return None

    def temporary_fillet(token, radius, fallback=None, capture=False):
        """Build a real fillet temporarily; copy its result before deleting it."""
        if radius < FILLET_MIN_CM * 0.5:
            return None
        edge = resolve_edge(token, fallback)
        if edge is None:
            return None
        feat = None
        try:
            comp = edge.body.parentComponent
            fillets = comp.features.filletFeatures
            coll = adsk.core.ObjectCollection.create()
            coll.add(edge)
            fi = fillets.createInput()
            fi.addConstantRadiusEdgeSet(
                coll, adsk.core.ValueInput.createByReal(radius), True)
            feat = fillets.add(fi)
            if feat is None:
                return None
            try:
                if feat.healthState == adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState:
                    return None
            except Exception:
                pass
            if not capture:
                return {"ok": True}
            if feat.bodies.count < 1:
                return None
            body = feat.bodies.item(0)
            mgr = adsk.fusion.TemporaryBRepManager.get()
            candidate = mgr.copy(body)
            if candidate is None:
                return None
            return {
                "ok": True,
                "body": candidate,
                "candidate_edges": sample_edges(body.edges),
                "fillet_edges": sample_created_face_edges(feat.faces),
            }
        except Exception:
            return None
        finally:
            if feat is not None:
                try:
                    feat.deleteMe()
                except Exception:
                    pass

    def valid_radius(token, radius, fallback=None):
        return temporary_fillet(token, radius, fallback, False) is not None

    def find_max_radius(token, body_size, initial, fallback=None):
        if not valid_radius(token, FILLET_MIN_CM, fallback):
            return None
        cap = max(1.0, body_size * 1.5, initial * 8.0)
        lo = FILLET_MIN_CM
        hi = min(max(initial, FILLET_MIN_CM * 2.0), cap)
        if valid_radius(token, hi, fallback):
            lo = hi
            while hi < cap:
                probe = min(cap, hi * 2.0)
                if probe <= hi + 1e-9:
                    break
                if valid_radius(token, probe, fallback):
                    lo = probe
                    hi = probe
                    if hi >= cap:
                        return max(FILLET_MIN_CM, hi * 0.995)
                else:
                    hi = probe
                    break
            if hi == lo:
                return max(FILLET_MIN_CM, lo * 0.995)
        for _ in range(SEARCH_STEPS):
            mid = (lo + hi) * 0.5
            if valid_radius(token, mid, fallback):
                lo = mid
            else:
                hi = mid
        return max(FILLET_MIN_CM, lo * 0.995)

    def ensure_limits(mark):
        g = m._geom.get(mark["id"])
        if not g:
            return False
        max_r = g.get("max_radius")
        if max_r is None:
            max_r = find_max_radius(
                g.get("edge_token"),
                g.get("body_size", mark.get("size", 1.0)),
                max(mark.get("amount", 0.2), 0.2),
                m._entity.get(mark["id"]))
            if max_r is None:
                return False
            g["max_radius"] = max_r

        safe = min(max(mark.get("amount", FILLET_MIN_CM), FILLET_MIN_CM), max_r)
        mark["amount"] = safe
        mark["last_valid_amount"] = min(
            max(mark.get("last_valid_amount", safe), FILLET_MIN_CM), max_r)

        it = m._inputs.itemById("d") if m._inputs is not None else None
        if it is not None:
            try:
                if abs(it.value - safe) > 1e-9:
                    it.value = safe
            except Exception:
                pass
            if not g.get("limits_installed"):
                try:
                    it.minimumValue = FILLET_MIN_CM
                    it.isMinimumValueInclusive = True
                    it.maximumValue = max_r
                    it.isMaximumValueInclusive = True
                    g["limits_installed"] = True
                except Exception:
                    pass
        return True

    def compute_real(mark):
        if mark.get("tool") != "fillet":
            try:
                polys = old_real_extrude_edges(m._entity.get(mark["id"]), mark["amount"])
                if polys:
                    m._geom[mark["id"]]["real"] = polys
                    return True
            except Exception:
                pass
            m._geom.get(mark["id"], {}).pop("real", None)
            return False

        g = m._geom.get(mark["id"], {})
        result = temporary_fillet(
            g.get("edge_token"), mark.get("amount", 0.0),
            m._entity.get(mark["id"]), True)
        if not result:
            return False
        g["candidate_body"] = result["body"]
        g["candidate_edges"] = result["candidate_edges"]
        g["fillet_edges"] = result["fillet_edges"]
        g["candidate_radius"] = mark["amount"]
        mark["last_valid_amount"] = mark["amount"]
        return True

    m._compute_real = compute_real

    def radius_callout(group, mark):
        a = mark["anchor"]
        s = mark.get("size", 3.0)
        (xx, xy, xz), (yx, yy, yz) = m._camera_xy()
        d = max(0.8, min(s * 0.28, 2.6))
        tip = (a[0] + (0.78 * xx + 0.45 * yx) * d,
               a[1] + (0.78 * xy + 0.45 * yy) * d,
               a[2] + (0.78 * xz + 0.45 * yz) * d)
        m._sketchy(group, [tuple(a), tip], WARN_RGB, 0.0,
                   mark["id"] * 911, weight=2, strokes=1)
        cp = adsk.core.Point3D.create(*tip)
        txt = "R = {:.2f} mm".format(mark.get("amount", 0.0) * 10.0)
        text = group.addText(txt, "Arial", max(0.45, min(s * 0.10, 0.9)),
                             m._label_transform(cp))
        text.color = m._solid(WARN_RGB)
        m._apply_billboard(text, cp)

    def draw_fillet(group, mark, rgb, amp):
        g = m._geom.get(mark["id"], {})
        if g.get("max_radius") is None:
            return

        max_r = g["max_radius"]
        requested = min(max(mark.get("amount", FILLET_MIN_CM), FILLET_MIN_CM), max_r)
        if abs(requested - mark.get("amount", requested)) > 1e-9:
            mark["amount"] = requested
            try:
                if m._inputs is not None:
                    m._inputs.itemById("d").value = requested
            except Exception:
                pass

        cached = (g.get("candidate_body") is not None and
                  g.get("candidate_radius") is not None and
                  abs(g["candidate_radius"] - mark["amount"]) <= 1e-7)
        if not cached and not compute_real(mark):
            last = min(max(mark.get("last_valid_amount", FILLET_MIN_CM),
                           FILLET_MIN_CM), max_r)
            mark["amount"] = last
            try:
                if m._inputs is not None:
                    m._inputs.itemById("d").value = last
            except Exception:
                pass
            if not compute_real(mark):
                return
            g = m._geom.get(mark["id"], {})

        candidate = g.get("candidate_body")
        if candidate is None:
            return
        try:
            cg = group.addBRepBody(candidate)
            cg.color = m._solid(CANDIDATE_RGB)
            cg.setOpacity(CANDIDATE_OPACITY, True)
        except Exception:
            pass

        for i, poly in enumerate(g.get("candidate_edges", [])):
            m._sketchy(group, poly, rgb, amp, mark["id"] * 600 + i,
                       weight=1, strokes=2)
        for i, poly in enumerate(g.get("fillet_edges", [])):
            m._sketchy(group, poly, FILLET_RGB, amp * 0.35,
                       mark["id"] * 900 + i, weight=2, strokes=1)
        radius_callout(group, mark)

    m._draw_fillet = draw_fillet
    m._DRAW["fillet"] = draw_fillet

    def fields(mark):
        out = old_fields(mark)
        if mark.get("tool") == "fillet" and out:
            max_r = m._geom.get(mark["id"], {}).get("max_radius")
            out[0]["min"] = round(FILLET_MIN_CM * 10.0, 2)
            if max_r is not None:
                out[0]["max"] = round(max_r * 10.0, 2)
        return out

    m._fields = fields

    def apply_edit(mark, key, value):
        if mark.get("tool") != "fillet":
            return old_apply_edit(mark, key, value)
        try:
            requested = float(value) / 10.0
        except Exception:
            return
        g = m._geom.get(mark["id"], {})
        max_r = g.get("max_radius")
        safe = max(requested, FILLET_MIN_CM)
        if max_r is not None:
            safe = min(safe, max_r)
        mark["amount"] = safe

    m._apply_edit = apply_edit

    def accept(mark):
        if mark.get("tool") != "fillet":
            return old_accept(mark)
        g = m._geom.get(mark["id"], {})
        edge = resolve_edge(g.get("edge_token"), m._entity.get(mark["id"]))
        if edge is None:
            m._ui.messageBox("FuzzyCAD lost the original fillet edge.")
            return False
        try:
            body = m._body.get(mark["id"])
            if body:
                body.opacity = 1.0
            comp = edge.body.parentComponent
            fil = comp.features.filletFeatures
            coll = adsk.core.ObjectCollection.create(); coll.add(edge)
            fi = fil.createInput()
            fi.addConstantRadiusEdgeSet(
                coll, adsk.core.ValueInput.createByReal(mark["amount"]), True)
            fil.add(fi)
            return True
        except Exception:
            m._ui.messageBox("FuzzyCAD couldn't apply the fillet:\n{}".format(
                m.traceback.format_exc()))
            return False

    m._accept = accept

    def draw_label(group, mark, rgb):
        if mark.get("tool") == "fillet":
            return
        return old_draw_label(group, mark, rgb)

    m._draw_label = draw_label

    class RealFilletPreview(adsk.core.CommandEventHandler):
        def notify(self, args):
            try:
                m._clear(m.GROUP_PREVIEW)
                group = m._group(m.GROUP_PREVIEW)
                if m._pending:
                    cats = m.CMD_CATS[m._active_cmd]
                    if "rotate" in cats:
                        rop = m._category_raw("rotate")
                        active = None if m._is_default("rotate", rop) else m._dominant_axis(rop["rot"])
                        m._draw_rotate_guides(group, m._pending["anchor"],
                                              m._pending["size"], active)
                    for cat in cats:
                        if cat == "fillet":
                            mid = m._live.get(cat)
                            mark = m._find(mid) if mid is not None else None
                            if mark is None:
                                continue
                            if not ensure_limits(mark):
                                args.isValidResult = False
                                return
                            if abs(m._val("d")) > 1e-9:
                                mark = m._sync_category(cat)
                            if mark is not None:
                                draw_fillet(group, mark, *m._style(mark))
                        elif cat == "extrude":
                            if abs(m._val("d")) > 1e-6:
                                m._sync_category(cat)
                            mid = m._live.get(cat)
                            mark = m._find(mid) if mid is not None else None
                            if mid is not None:
                                m._geom[mid].pop("real", None)
                            if mark is not None:
                                m._draw_one(group, mark)
                        else:
                            mark = m._sync_category(cat)
                            if mark is not None:
                                m._draw_one(group, mark)
                    m._refresh_ghost()
                    m._send_state()
                args.isValidResult = True
            except Exception:
                if m._ui:
                    m._ui.messageBox("FuzzyCAD preview failed:\n{}".format(
                        m.traceback.format_exc()))

    m.FuzzyPreview = RealFilletPreview
