"""Tune the hand-drawn proposal style to stay legible without becoming heavy.

The visual language keeps two slightly offset pencil strokes, but the normal
proposal outline now uses Fusion's lightest practical line weight.  Local
operation cues can still request stronger emphasis explicitly.
"""

import math
import random


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
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD SKETCH] " + msg)
        except Exception:
            pass

    def sketchy(group, pts, rgb, amp, seed, weight=2, strokes=2):
        n = len(pts)
        if n < 2:
            return
        if amp <= 0:
            strokes = 1
        elif strokes >= 2:
            # Preserve the handmade offset, but keep the proposal itself light.
            amp = float(amp) * 0.68
            strokes = min(int(strokes), 2)
            if int(weight) < 3:
                weight = 1

        color = m._solid(rgb)
        for s in range(strokes):
            rng = random.Random(seed * 131 + s * 977)
            waves = []
            for _ax in range(3):
                waves.append((0.7 + rng.random() * 0.6, rng.random() * 6.2832))
            flat = []
            for i, (x, y, z) in enumerate(pts):
                u = i / (n - 1)
                taper = math.sin(math.pi * u)
                base = [x, y, z]
                for ax in range(3):
                    f1, p1 = waves[ax]
                    base[ax] += amp * taper * math.sin(u * 6.2832 * f1 + p1)
                flat.extend(base)
            coords = adsk.fusion.CustomGraphicsCoordinates.create(flat)
            line = group.addLines(coords, list(range(n)), True)
            line.color = color
            line.weight = int(weight)

    m._sketchy = sketchy

    def run(context):
        result = old_run(context)
        log("SKETCH TUNING READY: 1px proposal strokes + restrained two-stroke wobble")
        return result

    m.run = run
