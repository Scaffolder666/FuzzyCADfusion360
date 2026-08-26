"""Persistent comic uncertainty visual for FuzzyCAD.

This file owns one visualization only: the paper/white fill plus seeded sketchy
boundary. It never decides lifecycle and it no longer changes the source body's
opacity. `fuzzycad_uncertainty_visual.py` decides what is visible and
`fuzzycad_opacity_runtime.py` applies the centrally derived source opacity.

Runtime groups are persistent per body and store only group-id strings/signatures
in Python; Fusion wrappers are resolved fresh when touched.
"""

import importlib.util
import math
import os
import random
import sys

FUZZY_ON        = True
COPIES_PER_BODY = 4
SCATTER         = 0.01
OVERSHOOT       = 0.0001
LINE_WEIGHT     = 2.0
GRAY_LIGHT      = 165
GRAY_DARK       = 45
SHOW_THROUGH    = False
SHOW_THRU_OPACITY = 0.6
FLAT_FILL       = True
FILL_RGB        = (222, 220, 214)
FILL_FLATTEN    = 0.55
MAX_LINES       = 2400


def _load_patch(m, name, relpath, installed_attr):
    if getattr(m, installed_attr, None) is not None:
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, *relpath.split("/"))
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    mod.install(m)


def _ensure_runtime_store(m):
    _load_patch(m, "fuzzycad_runtime_store", "core/fuzzycad_runtime_store.py", "_runtime_store")


def _ensure_visual_authority(m):
    _load_patch(
        m, "fuzzycad_uncertainty_visual", "visuals/fuzzycad_uncertainty_visual.py",
        "_uncertainty_visual_state")


def install(m):
    _ensure_runtime_store(m)
    _ensure_visual_authority(m)

    adsk = m.adsk
    old_redraw = m._redraw_marks
    old_refresh_ghost = m._refresh_ghost
    old_run = m.run
    old_stop = m.stop

    m._FUZZY_BOUNDARY = FUZZY_ON
    LEGACY_GID = "FuzzyCAD_FuzzyBoundary"
    lifecycle = {"legacy_cleaned": False}

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD COMIC] " + msg)
        except Exception:
            pass

    def cleanup_legacy_group():
        if lifecycle["legacy_cleaned"]:
            return False
        lifecycle["legacy_cleaned"] = True
        try:
            before = m._runtime_group_exists(LEGACY_GID)
            m._clear(LEGACY_GID)
            return bool(before)
        except Exception:
            return False

    def gray_for(k):
        if COPIES_PER_BODY <= 1:
            g = GRAY_DARK
        else:
            t = k / float(COPIES_PER_BODY - 1)
            g = int(round(GRAY_LIGHT + (GRAY_DARK - GRAY_LIGHT) * t))
        g = max(0, min(255, g))
        return (g, g, g)

    def overshoot(poly, ext):
        if ext <= 0 or len(poly) < 2:
            return poly

        def past(a, b):
            dx, dy, dz = a[0] - b[0], a[1] - b[1], a[2] - b[2]
            d = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            return (a[0] + dx / d * ext, a[1] + dy / d * ext, a[2] + dz / d * ext)

        return [past(poly[0], poly[1])] + list(poly) + [past(poly[-1], poly[-2])]

    def stroke(group, pts, rgb, seed, size):
        vs = getattr(m, "_visual_stroke", None)
        if vs is not None:
            vs(group, pts, "proposal_internal", seed, size=size, rgb=rgb,
               weight=LINE_WEIGHT, strokes=1)
        else:
            m._sketchy(group, pts, rgb, max(0.01, size * 0.004), seed,
                       weight=LINE_WEIGHT, strokes=1)

    def flat_material():
        def C(r, g, b):
            return adsk.core.Color.create(int(max(0, min(255, r))),
                                          int(max(0, min(255, g))),
                                          int(max(0, min(255, b))), 255)
        r, g, b = FILL_RGB
        f = max(0.0, min(1.0, FILL_FLATTEN))
        return adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
            C(r, g, b), C(r, g, b), C(0, 0, 0), C(r * f, g * f, b * f), 0.0, 1.0)

    def draw_fill(group, tmp, body):
        if not FLAT_FILL or tmp is None:
            return
        try:
            dup = tmp.copy(body)
            if dup is None:
                return
            cg = group.addBRepBody(dup)
            cg.color = flat_material()
        except Exception:
            log("flat fill failed\n{}".format(m.traceback.format_exc()))

    def style_signature():
        return (
            bool(getattr(m, "_FUZZY_BOUNDARY", True)),
            COPIES_PER_BODY, SCATTER, OVERSHOOT, LINE_WEIGHT,
            GRAY_LIGHT, GRAY_DARK, SHOW_THROUGH, SHOW_THRU_OPACITY,
            FLAT_FILL, tuple(FILL_RGB), FILL_FLATTEN, MAX_LINES,
        )

    def draw_body_group(tok, body, bi, geometry, line_budget):
        entry = m._runtime_render_entry(tok, "fuzzy", True)
        gid = entry["gid"]
        m._runtime_delete_group(gid)
        group = m._runtime_find_group(gid, True)
        if group is None:
            return False

        try:
            tmp = adsk.fusion.TemporaryBRepManager.get()
        except Exception:
            tmp = None
        draw_fill(group, tmp, body)

        size = float(geometry.get("size", 3.0))
        loops = geometry.get("edges") or []
        step = max(0.02, min(size * SCATTER, 0.80))
        ext = max(0.0, min(size * OVERSHOOT, 0.8))
        rnd = random.Random(1234 + bi * 97)
        drawn = 0

        for k in range(COPIES_PER_BODY):
            if drawn >= line_budget:
                break
            rgb = gray_for(k)
            dx = rnd.uniform(-1, 1); dy = rnd.uniform(-1, 1); dz = rnd.uniform(-1, 1)
            dl = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            mag = step * rnd.uniform(0.4, 1.1)
            ox, oy, oz = dx / dl * mag, dy / dl * mag, dz / dl * mag
            for j, poly in enumerate(loops):
                if drawn >= line_budget:
                    break
                pts = overshoot([(q[0] + ox, q[1] + oy, q[2] + oz) for q in poly], ext)
                try:
                    stroke(group, pts, rgb, (bi * 911 + k * 131 + j) & 0xffff, size)
                    drawn += 1
                except Exception:
                    pass

        if SHOW_THROUGH:
            try:
                eff = adsk.fusion.CustomGraphicsShowThroughColorEffect.create(
                    adsk.core.Color.create(GRAY_DARK, GRAY_DARK, GRAY_DARK, 255),
                    float(SHOW_THRU_OPACITY))
                for i in range(group.count):
                    try:
                        ent = group.item(i)
                        if "Line" in getattr(ent, "objectType", ""):
                            ent.showThrough = eff
                    except Exception:
                        pass
            except Exception:
                log("showThrough not applied\n{}".format(m.traceback.format_exc()))

        entry["signature"] = (
            style_signature(), geometry.get("signature"), int(bi), int(line_budget))
        entry["visible"] = True
        entry["dirty"] = False
        log("comic body lines={} body={} group={}".format(drawn, tok, gid))
        return True

    def allocate_budgets(prepared):
        """Share the global line guard without starving later comic bodies.

        Give every body roughly one edge pass before spending the remaining budget
        on the extra sketch copies. This preserves the fill+boundary invariant even
        in a heavy assembly without making the scene unbounded.
        """
        budgets = [0] * len(prepared)
        remaining = max(0, int(MAX_LINES))
        if not prepared or remaining <= 0:
            return budgets

        base_needs = [len(item[3].get("edges") or []) for item in prepared]
        total_base = sum(base_needs)

        if total_base <= remaining:
            for i, need in enumerate(base_needs):
                budgets[i] = need
                remaining -= need
        else:
            left = len(prepared)
            for i, need in enumerate(base_needs):
                share = max(1, remaining // max(1, left)) if remaining > 0 else 0
                give = min(need, share, remaining)
                budgets[i] = give
                remaining -= give
                left -= 1

        pending = True
        while remaining > 0 and pending:
            pending = False
            for i, item in enumerate(prepared):
                possible = int(COPIES_PER_BODY) * len(item[3].get("edges") or [])
                if budgets[i] >= possible:
                    continue
                pending = True
                step = min(len(item[3].get("edges") or []), possible - budgets[i], remaining)
                if step <= 0:
                    continue
                budgets[i] += step
                remaining -= step
                if remaining <= 0:
                    break
        return budgets

    def sync_fuzzy():
        changed = cleanup_legacy_group()
        try:
            m._runtime_sync_proposals()
        except Exception:
            pass

        if not getattr(m, "_FUZZY_BOUNDARY", True):
            for tok in list(m._runtime_render_tokens("fuzzy")):
                changed = m._runtime_drop_render(tok, "fuzzy", True) or changed
            return changed

        rows, retained = m._visual_comic_subject_rows()
        visible = set(tok for tok, _ in rows)

        # Only resolved/deleted subjects are destroyed. A non-comic Editing phase
        # merely hides the persistent group so Confirm can return to Proposed by a
        # cheap visibility toggle when geometry/style did not change.
        for tok in list(m._runtime_render_tokens("fuzzy")):
            if tok not in retained:
                changed = m._runtime_drop_render(tok, "fuzzy", True) or changed

        for tok in retained - visible:
            entry = m._runtime_render_entry(tok, "fuzzy", False)
            if entry is not None and m._runtime_group_exists(entry.get("gid")):
                actual = m._runtime_group_visible(entry["gid"])
                if entry.get("visible", False) or actual is True:
                    if m._runtime_set_group_visible(entry["gid"], False):
                        entry["visible"] = False
                        changed = True

        prepared = []
        for bi, (tok, body) in enumerate(rows):
            geometry = m._runtime_body_geometry(body)
            prepared.append((bi, tok, body, geometry))
        budgets = allocate_budgets(prepared)

        for idx, (bi, tok, body, geometry) in enumerate(prepared):
            budget = int(budgets[idx]) if idx < len(budgets) else 0
            entry = m._runtime_render_entry(tok, "fuzzy", True)
            signature = (style_signature(), geometry.get("signature"), int(bi), int(budget))
            gid = entry["gid"]
            exists = m._runtime_group_exists(gid)

            if (not exists) or entry.get("signature") != signature or entry.get("dirty", True):
                if draw_body_group(tok, body, bi, geometry, budget):
                    changed = True
                continue

            actual = m._runtime_group_visible(gid)
            if not entry.get("visible", False) or actual is False:
                if m._runtime_set_group_visible(gid, True):
                    entry["visible"] = True
                    changed = True

        return changed

    m._sync_comic_uncertainty = sync_fuzzy

    def refresh_ghost():
        # Opacity is owned by fuzzycad_opacity_runtime; keep this wrapper only so
        # existing call sites still synchronize the persistent comic graphics.
        old_refresh_ghost()

    m._refresh_ghost = refresh_ghost

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            changed = sync_fuzzy()
            if changed:
                m._app.activeViewport.refresh()
        except Exception:
            log("comic sync failed\n{}".format(m.traceback.format_exc()))
        return result

    m._redraw_marks = redraw

    def run(context):
        result = old_run(context)
        try:
            m._runtime_reset_graphics("FuzzyCAD_Runtime_fuzzy_")
            lifecycle["legacy_cleaned"] = False
            if sync_fuzzy():
                m._app.activeViewport.refresh()
        except Exception:
            log("startup comic sync failed\n{}".format(m.traceback.format_exc()))
        log("COMIC UNCERTAINTY READY: geometry-only renderer under central visual authority")
        return result

    m.run = run

    def stop(context):
        try:
            m._runtime_reset_graphics("FuzzyCAD_Runtime_fuzzy_")
            m._clear(LEGACY_GID)
        except Exception:
            pass
        return old_stop(context)

    m.stop = stop
