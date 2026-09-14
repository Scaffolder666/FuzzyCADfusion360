"""Reset stale FuzzyCAD runtime visuals before persisted collaboration state loads.

CustomGraphics are disposable presentation and can safely be removed at startup.
Body opacity is different: it is user-authored Fusion state, so startup restores
only values explicitly recorded by FuzzyCAD's opacity owner. It never guesses from
a numeric opacity such as 0.08, 0.16, or 0.50.
"""


def install(m):
    adsk = m.adsk
    old_run = m.run

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

    def all_components(design):
        if design is None:
            return []
        out = []
        try:
            comps = design.allComponents
            for i in range(comps.count):
                try:
                    out.append(comps.item(i))
                except Exception:
                    pass
        except Exception:
            try:
                out.append(design.rootComponent)
            except Exception:
                pass
        return out

    def clear_graphics(design):
        if design is None:
            return 0
        removed = 0
        for comp in all_components(design):
            try:
                groups = comp.customGraphicsGroups
            except Exception:
                continue
            for i in range(groups.count - 1, -1, -1):
                try:
                    group = groups.item(i)
                    gid = str(getattr(group, "id", "") or "")
                    if gid.startswith("FuzzyCAD"):
                        group.deleteMe()
                        removed += 1
                except Exception:
                    pass
        return removed

    def clear_runtime_state():
        # Restore only FuzzyCAD-owned opacity records. The opacity service enforces
        # the ownership boundary and leaves arbitrary user transparency untouched.
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
        log("STARTUP CLEAN removed_groups={} opacity_restored=recorded-only".format(
            removed))
        return result

    m.run = run
