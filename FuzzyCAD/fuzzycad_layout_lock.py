"""Reapply the preferred FuzzyCAD workspace layout on every add-in start.

Fusion remembers palette layouts imperfectly across add-in reloads/documents.  This
module treats the workspace arrangement as part of the FuzzyCAD interface:
- FuzzyCAD Tools stays docked on the left at a useful rail width;
- the collaboration/sidebar palette stays docked on the right;
- TEXT COMMANDS stays docked at the bottom at a compact debug height.

The layout is reapplied, not hard-locked: users can still resize panes during a
session.  The next FuzzyCAD run restores the preferred arrangement.
"""


def install(m):
    adsk = m.adsk
    old_run = m.run

    LEFT_WIDTH = 280
    RIGHT_WIDTH = 285
    TEXT_HEIGHT = 125

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD LAYOUT] " + msg)
        except Exception:
            pass

    def dock_state(name):
        try:
            return getattr(adsk.core.PaletteDockingStates, name)
        except Exception:
            try:
                return m._dock_state(name)
            except Exception:
                return None

    def set_docked_width(palette, state, width):
        if palette is None:
            return
        try:
            palette.isVisible = True
        except Exception:
            pass
        if state is not None:
            try:
                palette.dockingState = state
            except Exception:
                pass
        # Fusion can lock one dimension after docking. Try both APIs; whichever
        # the current build permits will take effect.
        try:
            palette.width = int(width)
        except Exception:
            pass
        try:
            h = max(500, int(getattr(palette, "height", 700) or 700))
            palette.setSize(int(width), h)
        except Exception:
            pass

    def find_native_browser(ui):
        """Find the built-in Browser for diagnostics without moving native UI."""
        try:
            pals = ui.palettes
            for i in range(pals.count):
                p = pals.item(i)
                try:
                    if p.isNative and (str(p.name).strip().lower() == "browser" or
                                       "browser" in str(p.id).lower()):
                        return p
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def apply_layout():
        ui = m._ui
        if ui is None:
            try:
                ui = adsk.core.Application.get().userInterface
            except Exception:
                return

        try:
            palettes = ui.palettes
        except Exception:
            return

        tools = None
        side = None
        text = None
        try: tools = palettes.itemById(m.TOOLBAR_ID)
        except Exception: pass
        try: side = palettes.itemById(m.PALETTE_ID)
        except Exception: pass
        try: text = palettes.itemById("TextCommands")
        except Exception: pass

        set_docked_width(tools, dock_state("PaletteDockStateLeft"), LEFT_WIDTH)
        set_docked_width(side, dock_state("PaletteDockStateRight"), RIGHT_WIDTH)

        if text is not None:
            try:
                text.isVisible = True
            except Exception:
                pass
            bottom = dock_state("PaletteDockStateBottom")
            if bottom is not None:
                try:
                    text.dockingState = bottom
                except Exception:
                    pass
            try:
                text.height = TEXT_HEIGHT
            except Exception:
                pass
            try:
                w = max(900, int(getattr(text, "width", 1200) or 1200))
                text.setSize(w, TEXT_HEIGHT)
            except Exception:
                pass

        browser = find_native_browser(ui)
        try:
            log("APPLY tools={}px left side={}px right text={}px bottom browser={}".format(
                getattr(tools, "width", -1) if tools else -1,
                getattr(side, "width", -1) if side else -1,
                getattr(text, "height", -1) if text else -1,
                getattr(browser, "id", "not-found") if browser else "not-found"))
        except Exception:
            pass

    m._apply_preferred_layout = apply_layout

    def run(context):
        result = old_run(context)
        # A first pass immediately after palette creation, then one UI flush and
        # a second pass. Fusion occasionally resolves docking geometry only after
        # processing the first layout event.
        try:
            apply_layout()
            adsk.doEvents()
            apply_layout()
        except Exception:
            log("layout apply failed\n{}".format(m.traceback.format_exc()))
        log("LAYOUT READY: left tools / right collaboration / bottom text commands")
        return result

    m.run = run
