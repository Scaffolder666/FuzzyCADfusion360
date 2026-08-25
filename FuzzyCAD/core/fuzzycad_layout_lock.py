"""Reapply the preferred FuzzyCAD workspace layout on every add-in start.

The study/runtime interface keeps FuzzyCAD Tools on the left and the
collaboration panel on the right. Fusion's Text Commands palette is development
instrumentation only and stays hidden unless FuzzyCAD.DEV_MODE is enabled.
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

    def configure_text_commands(text):
        if text is None:
            return
        dev = bool(getattr(m, "DEV_MODE", False))
        try:
            text.isVisible = dev
        except Exception:
            pass
        if not dev:
            return
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
        configure_text_commands(text)

        browser = find_native_browser(ui)
        try:
            log("APPLY tools={}px left side={}px right text_visible={} browser={}".format(
                getattr(tools, "width", -1) if tools else -1,
                getattr(side, "width", -1) if side else -1,
                bool(getattr(text, "isVisible", False)) if text else False,
                getattr(browser, "id", "not-found") if browser else "not-found"))
        except Exception:
            pass

    m._apply_preferred_layout = apply_layout

    def run(context):
        result = old_run(context)
        try:
            apply_layout()
            adsk.doEvents()
            apply_layout()
        except Exception:
            log("layout apply failed\n{}".format(m.traceback.format_exc()))
        log("LAYOUT READY: left tools / right collaboration / dev console={}".format(
            bool(getattr(m, "DEV_MODE", False))))
        return result

    m.run = run
