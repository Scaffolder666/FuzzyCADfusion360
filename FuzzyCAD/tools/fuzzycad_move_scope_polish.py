"""Polish the relationship-aware Move interaction.

The first prototype made two things ambiguous:
1) translucent related bodies looked as if they were already selected/together;
2) on some Fusion builds Move emits inputChanged without executePreview, so the
   native arrow value changes while the related candidate does not move.

This patch labels the related candidates R1/R2/... with strong orange outlines,
labels the primary body as Selected, and synchronizes Move directly from native
mX/mY/mZ inputChanged events.  Together therefore redraws the whole cached set
on every handle change; Only this leaves the related candidates stationary.
"""


def install(m):
    adsk = m.adsk
    CurrentInputChanged = m.FuzzyInputChanged
    CurrentPreview = m.FuzzyPreview
    CurrentLaunchHandler = m.LaunchHandler
    old_draw_move = m._DRAW.get("move")
    old_run = m.run

    ORANGE = (225, 126, 38)
    NAVY = (55, 78, 100)

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD MOVE POLISH] " + msg)
        except Exception:
            pass

    def transform_points(points, matrix):
        out = []
        for xyz in points:
            try:
                p = adsk.core.Point3D.create(*xyz)
                p.transformBy(matrix)
                out.append((p.x, p.y, p.z))
            except Exception:
                pass
        return out

    def body_center_size(body):
        try:
            return m._bbox_center_size(body)
        except Exception:
            return ([0.0, 0.0, 0.0], 3.0)

    def ensure_cache():
        if not m._pending:
            return []
        related = m._pending.get("related_bodies", [])
        cache = m._pending.get("related_visual_cache")
        if cache is not None and len(cache) == len(related):
            return cache
        cache = []
        for idx, body in enumerate(related):
            try:
                center, size = body_center_size(body)
                edges = m._sample_edges(body.edges)
                cache.append({"body": body, "center": center, "size": size,
                              "edges": edges, "label": "R{}".format(idx + 1)})
            except Exception:
                continue
        m._pending["related_visual_cache"] = cache
        return cache

    def label(group, xyz, text, size, rgb):
        try:
            p = adsk.core.Point3D.create(*xyz)
            t = group.addText(text, "Arial", max(0.42, min(size * 0.09, 0.75)),
                              m._label_transform(p))
            t.color = m._solid(rgb)
            m._apply_billboard(t, p)
        except Exception:
            pass

    def fill_body(group, body, opacity, matrix=None):
        """A solid orange wash over the whole related body, so the coupling is
        obvious at a glance rather than communicated by thin outlines alone."""
        if body is None:
            return
        try:
            cg = group.addBRepBody(body)
            if matrix is not None:
                cg.transform = matrix
            cg.color = m._solid(ORANGE)
            cg.setOpacity(opacity, True)
        except Exception:
            pass

    def draw_outline(group, item, matrix=None, moved=False):
        rgb = ORANGE
        fill_body(group, item.get("body"), 0.5 if moved else 0.4, matrix)
        for i, loop in enumerate(item.get("edges", [])):
            pts = transform_points(loop, matrix) if matrix is not None else loop
            if len(pts) >= 2:
                m._sketchy(group, pts, rgb, 0.0,
                           99000 + (i * 17) + (2000 if moved else 0),
                           weight=3 if moved else 2, strokes=1)
        center = item.get("center", [0, 0, 0])
        if matrix is not None:
            pts = transform_points([center], matrix)
            if pts:
                center = pts[0]
        label(group, center, item.get("label", "R"), item.get("size", 3.0), rgb)

    def primary_label(group, mark):
        try:
            label(group, mark.get("anchor", [0, 0, 0]), "Selected",
                  mark.get("size", 3.0), NAVY)
        except Exception:
            pass

    def relation_for(mark):
        if mark is not None and mark.get("related_bodies") is not None:
            related = mark.get("related_bodies", [])
            scope = mark.get("move_scope", "only")
        elif m._pending:
            related = m._pending.get("related_bodies", [])
            scope = m._pending.get("move_scope", "only")
        else:
            related, scope = [], "only"
        return related, scope

    def draw_move(group, mark, rgb, amp):
        if old_draw_move is not None:
            old_draw_move(group, mark, rgb, amp)
        related, scope = relation_for(mark)
        if not related:
            return
        cache = ensure_cache() if m._pending else []
        # Persisted cards may no longer have the pending cache. Rebuild cheaply.
        if not cache:
            for idx, body in enumerate(related):
                try:
                    c, s = body_center_size(body)
                    cache.append({"body": body, "center": c, "size": s,
                                  "edges": m._sample_edges(body.edges),
                                  "label": "R{}".format(idx + 1)})
                except Exception:
                    pass
        primary_label(group, mark)
        matrix = m._op_matrix(mark) if scope == "together" else None
        for item in cache:
            # Original location remains visibly identified as the related part.
            draw_outline(group, item, None, False)
            # In Together mode, an additional bold outline follows the move.
            if matrix is not None:
                draw_outline(group, item, matrix, True)

    m._DRAW["move"] = draw_move

    def attach_move(mark):
        if mark is None or not m._pending:
            return mark
        mark["related_bodies"] = list(m._pending.get("related_bodies", []))
        mark["move_scope"] = m._pending.get("move_scope", "only")
        return mark

    def redraw_live(no_refresh=True):
        m._clear(m.GROUP_PREVIEW)
        group = m._group(m.GROUP_PREVIEW)
        if group is None:
            return
        # Keep rotation guides available while using the combined Transform tool.
        try:
            if m._pending and "rotate" in m.CMD_CATS.get(m._active_cmd, ()):
                rop = m._category_raw("rotate")
                active = None if m._is_default("rotate", rop) else m._dominant_axis(rop["rot"])
                m._draw_rotate_guides(group, m._pending["anchor"], m._pending["size"], active)
        except Exception:
            pass
        for cat, mid in list(m._live.items()):
            mark = m._find(mid)
            if mark is None:
                continue
            if cat == "move":
                attach_move(mark)
            try:
                m._draw_one(group, mark)
            except Exception:
                pass
        m._refresh_ghost()
        m._send_state()
        # Never force refresh during native handle drag; Fusion releases the
        # manipulator on some builds. Selection-time refresh is safe.
        if not no_refresh:
            try:
                m._app.activeViewport.refresh()
            except Exception:
                pass

    def redraw_relation_selection():
        if not m._pending:
            return
        cache = ensure_cache()
        if not cache:
            return
        m._clear(m.GROUP_PREVIEW)
        group = m._group(m.GROUP_PREVIEW)
        if group is None:
            return
        try:
            if "rotate" in m.CMD_CATS.get(m._active_cmd, ()):
                m._draw_rotate_guides(group, m._pending["anchor"], m._pending["size"], None)
        except Exception:
            pass
        label(group, m._pending.get("anchor", [0, 0, 0]), "Selected",
              m._pending.get("size", 3.0), NAVY)
        for item in cache:
            draw_outline(group, item)
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    class FuzzyInputChanged(CurrentInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass
            super().notify(args)

            if getattr(m, "_active_cmd", None) != "transform" or not m._pending:
                return
            try:
                if cid == "sel":
                    # Relation detection has already happened in the wrapped
                    # handler. Make the candidates unambiguous immediately.
                    if m._pending.get("related_bodies"):
                        ensure_cache()
                        redraw_relation_selection()
                        log("RELATED LABELS ready count={}".format(
                            len(m._pending.get("related_bodies", []))))
                    return

                if cid in ("mX", "mY", "mZ"):
                    # Do not wait for executePreview: synchronize from the native
                    # distance input itself, exactly as the Fillet live fix does.
                    mark = m._sync_category("move")
                    if mark is not None:
                        attach_move(mark)
                        redraw_live(no_refresh=True)
                        v = mark.get("vec", [0, 0, 0])
                        log("LIVE MOVE scope={} mm=({}, {}, {}) related={}".format(
                            mark.get("move_scope", "only"),
                            round(v[0] * 10, 2), round(v[1] * 10, 2), round(v[2] * 10, 2),
                            len(mark.get("related_bodies", []))))
            except Exception:
                log("input sync failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyInputChanged = FuzzyInputChanged

    class FuzzyPreview(CurrentPreview):
        def notify(self, args):
            super().notify(args)
            try:
                if getattr(m, "_active_cmd", None) == "transform" and m._pending:
                    mid = m._live.get("move")
                    mark = m._find(mid) if mid is not None else None
                    if mark is not None:
                        attach_move(mark)
                        # Explicit redraw fixes the previous ordering where the
                        # card got the relation state only after the viewport draw.
                        redraw_live(no_refresh=True)
            except Exception:
                log("preview redraw failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyPreview = FuzzyPreview

    class LaunchHandler(CurrentLaunchHandler):
        def notify(self, args):
            # Switching FuzzyCAD tools means the current drag/proposal is done.
            # Persist the final scope/value on the card before the old command is
            # terminated; no geometry is applied until Accept.
            try:
                if getattr(m, "_active_cmd", None) == "transform" and m._pending:
                    mid = m._live.get("move")
                    mark = m._find(mid) if mid is not None else None
                    if mark is not None:
                        attach_move(mark)
                        m._send_state()
                        log("AUTO-FINISH proposal on tool switch mark={}".format(mid))
            except Exception:
                pass
            return super().notify(args)

    m.LaunchHandler = LaunchHandler

    def run(context):
        result = old_run(context)
        log("MOVE SCOPE POLISH READY: R1/R2 labels + direct handle sync + auto-finish on tool switch")
        return result

    m.run = run
