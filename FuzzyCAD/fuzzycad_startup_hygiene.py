"""Reset stale FuzzyCAD runtime visuals before persisted collaboration state loads.

CustomGraphics and body opacity can survive in a saved Fusion document even
though they are only a visualization cache.  The source of truth is the saved
FuzzyCAD state, so every add-in startup removes old FuzzyCAD graphics, restores
body opacities left by older ghost previews, clears transient Python state, and
then lets the persistence layer rebuild only the marks that actually exist.
"""


def install(m):
    adsk = m.adsk
    old_run = m.run

    GHOST_OPACITIES = (0.08, 0.16)

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg)
                return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD HYGIENE] " + msg)
        except Exception:
            pass

    def active_design():
        try:
            app = m._app or adsk.core.Application.get()
            product = app.activeProduct
            return product if isinstance(product, adsk.fusion.Design) else None
        except Exception:
            return None

    def clear_graphics(design):
        if design is None:
            return 0
        removed = 0
        try:
            groups = design.rootComponent.customGraphicsGroups
            for i in range(groups.count - 1, -1, -1):
                try:
                    group = groups.item(i)
                    gid = str(getattr(group, "id", "") or "")
                    # All of our runtime graphic caches use this namespace.
                    if gid.startswith("FuzzyCAD_"):
                        group.deleteMe()
                        removed += 1
                except Exception:
                    pass
        except Exception:
            pass
        return removed

    def restore_stale_ghost_opacity(design):
        """Undo opacity values written by old FuzzyCAD ghost previews.

        Persisted proposals are re-faded immediately afterwards by
        _refresh_ghost, so this cleanup only removes orphaned visual state.
        """
        if design is None:
            return 0
        restored = 0
        try:
            comps = design.allComponents
            for ci in range(comps.count):
                try:
                    bodies = comps.item(ci).bRepBodies
                except Exception:
                    continue
                for bi in range(bodies.count):
                    try:
                        body = bodies.item(bi)
                        opacity = float(body.opacity)
                        if any(abs(opacity - value) <= 1.0e-4 for value in GHOST_OPACITIES):
                            body.opacity = 1.0
                            restored += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return restored

    def clear_runtime_state():
        # A Stop -> Run cycle can reuse the Python module.  Never let old marks
        # survive independently of the document's persisted state.
        try:
            m._restore_all_bodies()
        except Exception:
            pass
        try:
            m._marks[:] = []
        except Exception:
            pass
        for name in ("_geom", "_entity", "_body", "_tool_count"):
            try:
                getattr(m, name).clear()
            except Exception:
                pass
        try:
            m._next_id = 1
            m._pending = None
            m._inputs = None
            m._active_cmd = None
            m._live = {}
            m._ghosted = {}
        except Exception:
            pass

    def run(context):
        result = old_run(context)
        design = active_design()
        removed = clear_graphics(design)
        restored = restore_stale_ghost_opacity(design)
        clear_runtime_state()
        try:
            m._send_state()
        except Exception:
            pass
        try:
            if m._app and m._app.activeViewport:
                m._app.activeViewport.refresh()
        except Exception:
            pass
        log("STARTUP CLEAN removed_groups={} restored_ghost_bodies={}".format(
            removed, restored))
        return result

    m.run = run
