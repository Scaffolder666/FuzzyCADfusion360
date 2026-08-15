"""Central visual system for FuzzyCAD.

This file is the single source of truth for proposal appearance. Geometry tools
should ask for semantic roles (current, proposal internal, proposal outer,
operation cue, scaffold, annotation, etc.) instead of choosing colors, line
weights, stroke counts, or wobble locally.

Edit VISUAL_TOKENS / SKETCH_RANDOM here to tune the whole interface.
"""

import math
import random


VISUAL_TOKENS = {
    "current_geometry": {"opacity": 0.5},
    "current_outline": {
        "rgb": (0, 0, 0), "weight": 1, "strokes": 2,
        "wobble_ratio": 0.0, "wobble_min": 0.0, "wobble_max": 0.0,
    },
    "proposal_internal": {
        "rgb": (0, 0, 0), "weight": 1.8, "strokes": 0,
        "wobble_ratio": 0.0028, "wobble_min": 0.0115, "wobble_max": 0.118,
    },
    "proposal_outer": {
        "rgb": (58, 62, 66), "weight": 3, "strokes": 1,
        "wobble_ratio": 0.0, "wobble_min": 0.0, "wobble_max": 0.0,
    },
    "surface_scaffold": {
        "rgb": (142, 146, 150), "weight": 1, "strokes": 1,
        "wobble_ratio": 0.0010, "wobble_min": 0.0008, "wobble_max": 0.008,
    },
    # Orange means where/how the change happens, never the whole proposal body.
    "operation_cue": {
        "rgb": (225, 126, 38), "weight": 2, "strokes": 1,
        "wobble_ratio": 0.0, "wobble_min": 0.0, "wobble_max": 0.0,
    },
    "affected_candidate": {
        "rgb": (225, 126, 38), "weight": 1, "strokes": 1,
        "wobble_ratio": 0.0, "wobble_min": 0.0, "wobble_max": 0.0,
    },
    # Local operations such as Fillet can use a surface patch plus a stronger
    # boundary while still sharing the same semantic orange family.
    "affected_surface": {
        "rgb": (235, 132, 42), "opacity": 0.46,
    },
    "affected_boundary": {
        "rgb": (245, 118, 24), "weight": 2, "strokes": 1,
        "wobble_ratio": 0.0, "wobble_min": 0.0, "wobble_max": 0.0,
    },
    "axis_reference": {
        "rgb": (125, 130, 135), "weight": 1, "strokes": 1,
        "wobble_ratio": 0.0, "wobble_min": 0.0, "wobble_max": 0.0,
    },
    "dimension": {
        "rgb": (92, 92, 92), "weight": 1, "strokes": 1,
        "wobble_ratio": 0.0, "wobble_min": 0.0, "wobble_max": 0.0,
    },
    "annotation": {
        "rgb": (77, 77, 77), "weight": 1, "strokes": 1,
        "wobble_ratio": 0.0, "wobble_min": 0.0, "wobble_max": 0.0,
    },
    "warning": {
        "rgb": (200, 44, 32), "weight": 2, "strokes": 1,
        "wobble_ratio": 0.0, "wobble_min": 0.0, "wobble_max": 0.0,
    },
    "label_current": {"rgb": (112, 118, 124)},
    "label_proposed": {"rgb": (55, 62, 68)},
}

SKETCH_RANDOM = {
    "frequency_min": 0.90,
    "frequency_max": 1.10,
    "phase_span": math.pi * 2.0,
    "seed_axis_step": 131,
    "seed_stroke_step": 977,
}

LEGACY_ROLE_BY_RGB = {
    (145, 145, 142): "current_outline",
    (70, 72, 74): "proposal_internal",
    (72, 76, 80): "proposal_outer",
    (118, 122, 126): "surface_scaffold",
    (142, 146, 150): "surface_scaffold",
    (125, 130, 135): "axis_reference",
    (92, 92, 92): "dimension",
    (77, 77, 77): "annotation",
    (200, 44, 32): "warning",
    (245, 118, 24): "affected_boundary",
}


def install(m):
    adsk = m.adsk
    old_run = m.run

    def log(msg):
        try:
            fn = getattr(m, "_debug", None)
            if fn:
                fn(msg); return
        except Exception:
            pass
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD VISUAL SYSTEM] " + msg)
        except Exception:
            pass

    m.VISUAL_TOKENS = VISUAL_TOKENS
    m.SKETCH_RANDOM = SKETCH_RANDOM
    m.GHOST_OPACITY = float(VISUAL_TOKENS["current_geometry"]["opacity"])

    def style(role):
        return VISUAL_TOKENS.get(role, VISUAL_TOKENS["proposal_internal"])

    def color(role):
        return tuple(style(role).get("rgb", (86, 90, 94)))

    def role_amp(role, size):
        st = style(role)
        ratio = float(st.get("wobble_ratio", 0.0))
        if ratio <= 0.0:
            return 0.0
        value = max(0.0, float(size or 0.0)) * ratio
        lo = float(st.get("wobble_min", 0.0))
        hi = float(st.get("wobble_max", value if value > 0 else lo))
        if hi < lo:
            hi = lo
        return max(lo, min(value, hi))

    def raw_stroke(group, pts, rgb, amp, seed, weight=1, strokes=1):
        n = len(pts)
        if n < 2:
            return
        amp = max(0.0, float(amp or 0.0))
        strokes = max(1, int(strokes or 1))
        if amp <= 1e-12:
            strokes = 1
        color_obj = m._solid(tuple(rgb))
        fmin = float(SKETCH_RANDOM["frequency_min"])
        fmax = float(SKETCH_RANDOM["frequency_max"])
        phase_span = float(SKETCH_RANDOM["phase_span"])
        axis_step = int(SKETCH_RANDOM["seed_axis_step"])
        stroke_step = int(SKETCH_RANDOM["seed_stroke_step"])
        for s in range(strokes):
            rng = random.Random(int(seed) * axis_step + s * stroke_step)
            waves = []
            for _axis in range(3):
                waves.append((fmin + rng.random() * (fmax - fmin),
                              rng.random() * phase_span))
            flat = []
            for i, xyz in enumerate(pts):
                u = i / float(max(1, n - 1))
                taper = math.sin(math.pi * u)
                base = [float(xyz[0]), float(xyz[1]), float(xyz[2])]
                for axis in range(3):
                    freq, phase = waves[axis]
                    base[axis] += amp * taper * math.sin(u * math.pi * 2.0 * freq + phase)
                flat.extend(base)
            coords = adsk.fusion.CustomGraphicsCoordinates.create(flat)
            line = group.addLines(coords, list(range(n)), True)
            line.color = color_obj
            line.weight = max(1, int(weight or 1))

    def visual_stroke(group, pts, role, seed, size=3.0, amp=None,
                      rgb=None, weight=None, strokes=None):
        st = style(role)
        actual_rgb = tuple(rgb if rgb is not None else st.get("rgb", (86, 90, 94)))
        actual_weight = int(weight if weight is not None else st.get("weight", 1))
        actual_strokes = int(strokes if strokes is not None else st.get("strokes", 1))
        actual_amp = role_amp(role, size) if amp is None else max(0.0, float(amp))
        return raw_stroke(group, pts, actual_rgb, actual_amp, seed,
                          weight=actual_weight, strokes=actual_strokes)

    def legacy_role(rgb, weight):
        key = tuple(int(x) for x in rgb)
        if key == (225, 126, 38):
            return "affected_candidate" if int(weight or 1) <= 1 else "operation_cue"
        if key in LEGACY_ROLE_BY_RGB:
            return LEGACY_ROLE_BY_RGB[key]
        if max(key) - min(key) <= 18 and sum(key) / 3.0 < 125:
            return "proposal_internal"
        return None

    def sketchy_compat(group, pts, rgb, amp, seed, weight=2, strokes=2):
        role = legacy_role(rgb, weight)
        if role:
            size = 3.0
            try:
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
                size = max(max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs), 0.25)
            except Exception:
                pass
            return visual_stroke(group, pts, role, seed, size=size)
        return raw_stroke(group, pts, rgb, amp, seed, weight=weight, strokes=strokes)

    m._visual_style = style
    m._visual_color = color
    m._visual_amp = role_amp
    m._visual_stroke = visual_stroke
    m._sketchy = sketchy_compat

    def run(context):
        result = old_run(context)
        log("CENTRAL VISUAL SYSTEM READY: all known legacy strokes normalized through semantic tokens")
        return result

    m.run = run
