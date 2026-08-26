"""Ghost replacement: a fuzzy, comic-style boundary (the default 'unsettled' look).

Appearance is intentionally unchanged: the same flat paper/putty fill, four
seeded offset sketch copies, line weights, grey range, overshoot and occlusion
rules are used. The lifecycle is different: each questioned body owns a stable
runtime graphics group. Card/tool switching toggles that group's visibility;
geometry is sampled into a pure-Python cache and rebuilt only when the body or
visual inputs actually change.

No Fusion CustomGraphics/BRep wrapper is retained in Python state. Runtime data
stores only entity tokens, XYZ arrays, group-id strings, signatures and flags;
Fusion groups are resolved fresh by id whenever they must be touched.
"""

import importlib.util
import math
import os
import random
import sys

FUZZY_ON        = True
HIDE_BODY       = False
BODY_OPACITY    = 0.00
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


def _ensure_runtime_store(m):
    if getattr(m, "_runtime_store", None) is not None:
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "core", "fuzzycad_runtime_store.py")
    name = "fuzzycad_runtime_store"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    mod.install(m)


def install(m):
    _ensure_runtime_store(m)

    adsk = m.adsk
    old_redraw = m._redraw_marks
    old_refresh_ghost = m._refresh_ghost
    old_run = m.run
    old_stop = m.stop

    m._FUZZY_BOUNDARY = FUZZY_ON
    LEGACY_GID = "FuzzyCAD_FuzzyBoundary"
    hidden_tokens = set()
    lifecycle = {"legacy_cleaned": False}

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD FUZZY] " + msg)
        except Exception:
            pass

    def body_tok(body):
        try:
            return m._runtime_entity_token(body) or "id:{}".format(id(body))
        except Exception:
            try:
                return str(body.entityToken)
            except Exception:
                return "id:{}".format(id(body))

    def resolve_body(tok):
        try:
            design = m._design()
            if design is None or not tok or str(tok).startswith("id:"):
                return None
            for ent in design.findEntityByToken(tok):
                if isinstance(ent, adsk.fusion.BRepBody):
                    return ent
        except Exception:
            pass
        return None

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

    def related_subjects(mark):
        """Additional bodies that are part of the same proposal subject set.

        Move Together already treats confirmed related bodies as one proposal in
        the collaboration data model. They must therefore receive the same comic
        boundary as the primary body while proposed. Keeping this here also avoids
        a split-brain state where a related body is faded but has no outline.
        """
        if mark.get("tool") != "move" or mark.get("move_scope") != "together":
            return []
        out = []
        for body in mark.get("related_bodies") or []:
            if body is not None:
                out.append(body)
        return out

    def open_fuzzy_tokens():
        out = set()
        for mark in list(getattr(m, "_marks", None) or []):
            if mark.get("status", "open") != "open":
                continue
            if mark.get("tool") in ("note", "fillet"):
                continue
            body = m._body.get(mark.get("id"))
            if body is not None:
                out.add(body_tok(body))
            for related in related_subjects(mark):
                out.add(body_tok(related))
        return out

    def questioned_rows():
        """Visible proposed fuzzy bodies in stable order.

        Primary subjects are collected first in exactly the legacy order so their
        seeded sketch offsets remain unchanged. Group-related subjects are appended
        afterward, which restores the missing outlines without perturbing existing
        primary-body line placement.
        """
        marks = list(getattr(m, "_marks", None) or [])
        out, seen = [], set()

        # Pass 1: original primary-body order.
        for mark in marks:
            if mark.get("status", "open") != "open" or mark.get("tool") == "note":
                continue
            try:
                if mark.get("tool") == "fillet" or m._mark_phase(mark) == "editing":
                    continue
            except Exception:
                pass
            body = m._body.get(mark.get("id"))
            if body is None:
                continue
            tok = body_tok(body)
            if tok in seen:
                continue
            seen.add(tok)
            out.append((tok, body))

        # Pass 2: proposal-group subjects (currently Move Together).
        for mark in marks:
            if mark.get("status", "open") != "open" or mark.get("tool") in ("note", "fillet"):
                continue
            try:
                if m._mark_phase(mark) == "editing":
                    continue
            except Exception:
                pass
            for body in related_subjects(mark):
                tok = body_tok(body)
                if tok in seen:
                    continue
                seen.add(tok)
                out.append((tok, body))
        return out

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
            bool(getattr(m, "_FUZZY_BOUNDARY", True)), HIDE_BODY, BODY_OPACITY,
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
        log("ghost lines drawn={} body={} group={}".format(drawn, tok, gid))
        return True

    def apply_body_state(rows=None):
        if not getattr(m, "_FUZZY_BOUNDARY", True):
            return
        rows = questioned_rows() if rows is None else rows
        want = set(tok for tok, _ in rows)
        for tok, body in rows:
            try:
                if HIDE_BODY:
                    hidden_tokens.add(tok)
                    body.isVisible = False
                else:
                    body.opacity = max(0.02, float(BODY_OPACITY))
            except Exception:
                pass
        for tok in list(hidden_tokens):
            if tok not in want:
                hidden_tokens.discard(tok)
                body = resolve_body(tok)
                try:
                    if body is not None and body.isValid:
                        body.isVisible = True
                except Exception:
                    pass

    def restore_all_visibility():
        for tok in list(hidden_tokens):
            hidden_tokens.discard(tok)
            body = resolve_body(tok)
            try:
                if body is not None and body.isValid:
                    body.isVisible = True
            except Exception:
                pass

    m._fuzzy_restore_visibility = restore_all_visibility

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

        rows = questioned_rows()
        apply_body_state(rows)
        retained = open_fuzzy_tokens()
        visible = set(tok for tok, _ in rows)

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

        remaining = int(MAX_LINES)
        for bi, (tok, body) in enumerate(rows):
            if remaining <= 0:
                entry = m._runtime_render_entry(tok, "fuzzy", False)
                if entry is not None and m._runtime_group_exists(entry.get("gid")):
                    actual = m._runtime_group_visible(entry["gid"])
                    if entry.get("visible", False) or actual is True:
                        if m._runtime_set_group_visible(entry["gid"], False):
                            entry["visible"] = False
                            changed = True
                continue

            geometry = m._runtime_body_geometry(body)
            loops = geometry.get("edges") or []
            possible = max(0, int(COPIES_PER_BODY) * len(loops))
            budget = min(remaining, possible)
            remaining -= budget

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

    def refresh_ghost():
        old_refresh_ghost()
        try:
            apply_body_state()
        except Exception:
            pass

    m._refresh_ghost = refresh_ghost

    def redraw(*args, **kwargs):
        result = old_redraw(*args, **kwargs)
        try:
            changed = sync_fuzzy()
            if changed:
                m._app.activeViewport.refresh()
        except Exception:
            log("fuzzy sync failed\n{}".format(m.traceback.format_exc()))
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
            log("startup fuzzy sync failed\n{}".format(m.traceback.format_exc()))
        log("FUZZY BOUNDARY READY: {:.0%} real body + persistent per-body sketchy grey line-ghost".format(
            BODY_OPACITY))
        return result

    m.run = run

    def stop(context):
        try:
            restore_all_visibility()
            m._runtime_reset_graphics("FuzzyCAD_Runtime_fuzzy_")
            m._clear(LEGACY_GID)
        except Exception:
            pass
        return old_stop(context)

    m.stop = stop
