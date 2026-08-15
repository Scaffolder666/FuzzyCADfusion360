"""Viewport badge semantics for FuzzyCAD uncertainty marks.

Need Input already uses a strong image badge in the 3D view. Keep that visual
language consistent for the other collaboration states as well:
- Note / Constraint uses a compact dark notebook icon with true transparency.
- Compare / Conflict uses icons/conflict.png.

The Note asset is embedded here and materialized into the OS temp directory at
runtime. This avoids the white-background/white-block behavior seen with the
previous generated PNG while keeping the same screen-constant Fusion point-sprite
renderer used by Need Input.
"""

import base64
import os
import tempfile

# 64x64 RGBA notebook + pencil. Dark strokes, fully transparent background.
_NOTE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAABnUlEQVR4nO2ZUQ6DMAiG"
    "2bKb1PufSM/inkhM0wot0GLlezFZ1PL/AmUKEARBEATBW/lIb5BSOjUC6eE4DnH8X8nF"
    "M8Vrrd9twGzxiDSOLgO8iEck8fw0AtCoxVa0HoKoB6xAGDA7gNmQPYBTaymlc0Yf0CA"
    "ywHoBrW5tlWGmGaA5L1jNHmQGlJzPg3lq/QMYZ4CmMVYmm/cA79nx+l0gDJgdwGxeP"
    "wm6HoRaTcUH0fJAXA9CLdfjufmRYukewDGhq25bJkGrEmi57919lhuEsAdcf7vrCUuV"
    "AIrk/H9BHmlATcz199yEZTKAqv3aLrBEBnAbX+m8WgaQDYq7aG0BrW5ttZu4GoQ4tV1"
    "j3/eiSGoXcl8CXPHbtrHT/oqrN0L5+dbiAQZMgr2MEA/gtARGiQdwaMBI8QDODBgtHsC"
    "RASXx+dZWEy/BjQE5KP561BYPMGAS5EJlACX+0SVQMxlFWzx5xIUBd2g2vBLuDKDEaQ"
    "9dLr8Ol15rWawDoDQKe2BoE/T2FUgST3cP8GKCNA5RE5xtwuz1gyAIgiAIHs0fYCEPfy"
    "wxBNoAAAAASUVORK5CYII=")


def install(m):
    old_draw_badge = m._draw_badge
    old_icon_path = m._icon_path

    try:
        m.MTYPE_LABEL["conflict"] = "Conflict"
        m.MTYPE_COLOR["conflict"] = (128, 90, 180)
        m.MTYPE_GLYPH["conflict"] = u"⑂"
    except Exception:
        pass

    note_icon = None
    try:
        raw = base64.b64decode(_NOTE_PNG_B64)
        note_icon = os.path.join(tempfile.gettempdir(), "fuzzycad_note_badge_64.png")
        write = True
        try:
            if os.path.exists(note_icon):
                with open(note_icon, "rb") as fh:
                    write = fh.read() != raw
        except Exception:
            write = True
        if write:
            with open(note_icon, "wb") as fh:
                fh.write(raw)
    except Exception:
        note_icon = None

    def icon_path(mtype):
        if mtype == "constraint" and note_icon and os.path.exists(note_icon):
            return note_icon
        return old_icon_path(mtype)

    # _draw_badge resolves this global through the legacy module at call time.
    m._icon_path = icon_path

    def draw_badge(group, mark):
        if mark is None:
            return

        tool = mark.get("tool")
        if tool == "note":
            # The legacy renderer suppresses Note based on the tool name. A
            # presentation copy reuses its exact image placement and billboard
            # behavior while allowing the notebook image to render.
            presentation = dict(mark)
            presentation["tool"] = "note_badge"
            presentation["mtype"] = "constraint"
            return old_draw_badge(group, presentation)

        if tool == "compare":
            # New Compare marks already use mtype=conflict, but persisted marks
            # from older builds may still say alternative. Always show Conflict.
            presentation = dict(mark)
            presentation["mtype"] = "conflict"
            return old_draw_badge(group, presentation)

        return old_draw_badge(group, mark)

    m._draw_badge = draw_badge
