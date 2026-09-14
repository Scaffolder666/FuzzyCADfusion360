"""Let work built on a fuzzy part follow it when the part is resolved.

Uncertainty is transitive in async CAD. If a collaborator builds new geometry on
an object while that object's Move/Rotate/Scale decision is still unresolved,
accepting the upstream decision must carry the downstream work so the relationship
does not silently break.

Selection-time snapshots store Fusion entity tokens only as persistent handles.
At Accept those handles are resolved back to Fusion entities and the entities are
compared. Token strings are never used as entity identity because Fusion can return
different token strings for the same entity over time.
"""

import math


def install(m):
    adsk = m.adsk
    old_accept = m._accept

    DEP_GID = "FuzzyCAD_FollowHighlight"
    FOLLOW_RGB = (225, 126, 38)

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD FOLLOW] " + msg)
        except Exception:
            pass

    def body_token(b):
        try:
            return b.entityToken
        except Exception:
            return None

    def same_entity(a, b):
        if a is None or b is None:
            return False
        if a is b:
            return True
        try:
            return bool(a == b)
        except Exception:
            return False

    def native_body(body):
        if body is None:
            return None
        try:
            native = body.nativeObject
            return native if native is not None else body
        except Exception:
            return body

    def body_occurrence(body):
        try:
            return adsk.fusion.Occurrence.cast(body.assemblyContext)
        except Exception:
            return None

    def same_body_instance(a, b):
        """Compare body identity without comparing entity-token strings.

        Two assembly proxies count as the same body only when both their native
        body and occurrence context match. A native body and one occurrence proxy
        are intentionally different instances.
        """
        if same_entity(a, b):
            return True
        if a is None or b is None:
            return False
        if not same_entity(native_body(a), native_body(b)):
            return False
        oa, ob = body_occurrence(a), body_occurrence(b)
        if oa is None and ob is None:
            return True
        if oa is None or ob is None:
            return False
        return same_entity(oa, ob)

    def contains_body(rows, body):
        return any(same_body_instance(row, body) for row in rows)

    def resolve_token_bodies(design, tokens):
        """Resolve a saved token snapshot into current body entities.

        A token remains a valid lookup handle even if Fusion would now emit a
        different token string for that same entity. Resolve first, compare second.
        """
        out = []
        if design is None:
            return out
        for tok in tokens or []:
            if not tok:
                continue
            try:
                ents = design.findEntityByToken(str(tok))
            except Exception:
                continue
            try:
                for ent in ents:
                    body = adsk.fusion.BRepBody.cast(ent)
                    if body is not None and not contains_body(out, body):
                        out.append(body)
            except Exception:
                continue
        return out

    def bbox_gap(a, b):
        amn, amx = a.minPoint, a.maxPoint
        bmn, bmx = b.minPoint, b.maxPoint
        dx = max(0.0, bmn.x - amx.x, amn.x - bmx.x)
        dy = max(0.0, bmn.y - amx.y, amn.y - bmx.y)
        dz = max(0.0, bmn.z - amx.z, amn.z - bmx.z)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def all_bodies(design):
        """Every solid body instance: root bodies plus occurrence proxies."""
        out = []
        try:
            root = design.rootComponent
            for i in range(root.bRepBodies.count):
                body = root.bRepBodies.item(i)
                if body is not None and not contains_body(out, body):
                    out.append(body)
            occs = root.allOccurrences
            for i in range(occs.count):
                try:
                    bs = occs.item(i).bRepBodies
                    for j in range(bs.count):
                        body = bs.item(j)
                        if body is not None and not contains_body(out, body):
                            out.append(body)
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def detect_dependents(primary):
        """Bodies currently touching the marked body, with instance-safe de-dupe."""
        if primary is None:
            return []
        design = m._design()
        if design is None:
            return []
        try:
            _, size = m._bbox_center_size(primary)
            pbb = primary.boundingBox
        except Exception:
            return []
        tol = max(0.05, min(float(size) * 0.02, 0.20))
        out = []
        for b in all_bodies(design):
            try:
                if same_body_instance(b, primary) or contains_body(out, b):
                    continue
                if hasattr(b, "isVisible") and not b.isVisible:
                    continue
                try:
                    if m._body_locked(b):
                        continue
                except Exception:
                    pass
                if bbox_gap(pbb, b.boundingBox) <= tol:
                    out.append(b)
            except Exception:
                continue
        log("DETECT dependents primary={} found={} tol_mm={:.2f}".format(
            getattr(primary, "name", "body"), len(out), tol * 10.0))
        return out[:12]

    m._follow_detect_dependents = detect_dependents

    def all_body_tokens(design=None):
        """Lookup handles for every body instance present when a mark is created."""
        design = design or m._design()
        toks = []
        if design is None:
            return toks
        for b in all_bodies(design):
            tok = body_token(b)
            if tok:
                toks.append(tok)
        return toks

    m._follow_all_tokens = all_body_tokens

    def highlight(bodies, on):
        try:
            m._clear(DEP_GID)
        except Exception:
            return
        if not on:
            try:
                m._app.activeViewport.refresh()
            except Exception:
                pass
            return
        grp = m._group(DEP_GID)
        if grp is None:
            return
        for b in bodies:
            try:
                cg = grp.addBRepBody(b)
                cg.color = m._solid(FOLLOW_RGB)
                cg.setOpacity(0.45, True)
            except Exception:
                continue
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def confirm(count):
        try:
            res = m._ui.messageBox(
                "{} part{} built on this were found.\n\n"
                "Carry them along with this change so they stay attached?".format(
                    count, " is" if count == 1 else "s are"),
                "FuzzyCAD — dependent parts",
                adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                adsk.core.MessageBoxIconTypes.QuestionIconType)
            return res == adsk.core.DialogResults.DialogYes
        except Exception:
            return False

    def rigid_matrix(mark):
        if mark.get("tool") == "axis_rotate":
            g = m._geom.get(mark["id"], {})
            origin = g.get("axis_origin") or mark.get("axis_origin") or [0.0, 0.0, 0.0]
            direction = g.get("axis_dir") or mark.get("axis_dir") or [0.0, 0.0, 1.0]
            mat = adsk.core.Matrix3D.create()
            mat.setToRotation(
                math.radians(float(mark.get("angle", 0.0))),
                adsk.core.Vector3D.create(*direction),
                adsk.core.Point3D.create(*origin))
            return mat
        return m._op_matrix(mark)

    def apply_together(matrix, primary, deps):
        """Apply the same rigid transform to primary and confirmed dependants."""
        try:
            groups = []
            added = []

            def enroll(b):
                if b is None or contains_body(added, b):
                    return
                added.append(b)
                try:
                    comp = b.parentComponent
                except Exception:
                    return
                for g in groups:
                    if g[0] == comp:
                        g[1].append(b)
                        return
                groups.append((comp, [b]))

            enroll(primary)
            for b in deps:
                enroll(b)

            moved = 0
            for comp, bodies in groups:
                coll = adsk.core.ObjectCollection.create()
                for b in bodies:
                    coll.add(b)
                comp.features.moveFeatures.add(
                    comp.features.moveFeatures.createInput(coll, matrix))
                moved += coll.count
            log("APPLIED move/rotate to {} bodies across {} component(s)".format(
                moved, len(groups)))
            return True
        except Exception:
            m._ui.messageBox("FuzzyCAD couldn't carry the dependent parts:\n{}".format(
                m.traceback.format_exc()))
            return False

    def resolve_body(design, tok):
        if design is None or not tok:
            return None
        try:
            ents = design.findEntityByToken(str(tok))
        except Exception:
            return None
        try:
            for e in ents:
                body = adsk.fusion.BRepBody.cast(e)
                if body is not None:
                    return body
        except Exception:
            pass
        return None

    def carry_bodies(matrix, specs, design):
        """Carry selected current bodies after the primary's own feature commits."""
        groups = []
        added = []

        def enroll(b):
            if b is None or contains_body(added, b):
                return
            added.append(b)
            try:
                comp = b.parentComponent
            except Exception:
                return
            for g in groups:
                if g[0] == comp:
                    g[1].append(b)
                    return
            groups.append((comp, [b]))

        for ref, tok in specs:
            target = ref
            try:
                if target is None or not target.isValid:
                    target = None
            except Exception:
                target = None
            if target is None:
                target = resolve_body(design, tok)
            if target is None:
                log("carry skip: body lost handle={}".format(tok))
                continue
            enroll(target)

        moved = 0
        for comp, bodies in groups:
            try:
                coll = adsk.core.ObjectCollection.create()
                for b in bodies:
                    coll.add(b)
                comp.features.moveFeatures.add(
                    comp.features.moveFeatures.createInput(coll, matrix))
                moved += coll.count
            except Exception:
                log("carry move failed\n{}".format(m.traceback.format_exc()))
        return moved

    # ---- non-rigid follow (Scale / Extrude): per-body displacement ---------
    def normalize(v):
        n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
        return [v[0] / n, v[1] / n, v[2] / n]

    def displacement(mark, p):
        tool = mark.get("tool")
        if tool == "scale":
            c = mark.get("anchor", [0.0, 0.0, 0.0])
            f = float(mark.get("factor", 1.0))
            return [(f - 1.0) * (p[i] - c[i]) for i in range(3)]
        if tool == "scale_axis":
            base = mark.get("base_anchor") or mark.get("anchor") or [0.0, 0.0, 0.0]
            f = float(mark.get("factor", 1.0))
            idx = {"X": 0, "Y": 1, "Z": 2}.get(mark.get("axis", "X"), 0)
            d = [0.0, 0.0, 0.0]
            d[idx] = (f - 1.0) * (p[idx] - base[idx])
            return d
        if tool == "extrude":
            n = normalize(m._geom.get(mark["id"], {}).get("normal", [0.0, 0.0, 1.0]))
            amt = float(mark.get("amount", 0.0))
            return [n[i] * amt for i in range(3)]
        return [0.0, 0.0, 0.0]

    def translate_body(body, disp):
        comp = body.parentComponent
        coll = adsk.core.ObjectCollection.create()
        coll.add(body)
        mtx = adsk.core.Matrix3D.create()
        mtx.translation = adsk.core.Vector3D.create(disp[0], disp[1], disp[2])
        comp.features.moveFeatures.add(
            comp.features.moveFeatures.createInput(coll, mtx))

    def detect_flex_deps(mark, primary):
        tool = mark.get("tool")
        design = m._design()
        if design is None or primary is None:
            return []
        try:
            _, size = m._bbox_center_size(primary)
        except Exception:
            size = 3.0
        tol = max(0.05, min(float(size) * 0.02, 0.20))
        if tool == "extrude":
            ent = m._entity.get(mark["id"])
            try:
                target = ent.boundingBox
            except Exception:
                return []
        else:
            try:
                target = primary.boundingBox
            except Exception:
                return []

        out = []
        for b in all_bodies(design):
            try:
                if same_body_instance(b, primary) or contains_body(out, b):
                    continue
                if hasattr(b, "isVisible") and not b.isVisible:
                    continue
                try:
                    if m._body_locked(b):
                        continue
                except Exception:
                    pass
                if bbox_gap(target, b.boundingBox) <= tol:
                    out.append(b)
            except Exception:
                continue
        log("DETECT flex deps tool={} found={}".format(tool, len(out)))
        return out[:12]

    def apply_flex(mark, deps):
        design = m._design()
        plan = []
        for b in deps:
            try:
                c = m._bbox_center_size(b)[0]
                disp = displacement(mark, c)
                plan.append((b, body_token(b), disp))
                log("FLEX plan handle={} disp=({:.3f}, {:.3f}, {:.3f})".format(
                    body_token(b), disp[0], disp[1], disp[2]))
            except Exception:
                log("FLEX plan failed\n{}".format(m.traceback.format_exc()))
        ok = old_accept(mark)
        moved = 0
        if ok:
            for body, tok, disp in plan:
                if all(abs(x) <= 1e-9 for x in disp):
                    continue
                target = body
                try:
                    if not target.isValid:
                        target = None
                except Exception:
                    target = None
                if target is None:
                    target = resolve_body(design, tok)
                if target is None:
                    log("FLEX skip: body lost handle={}".format(tok))
                    continue
                try:
                    translate_body(target, disp)
                    moved += 1
                except Exception:
                    log("FLEX translate failed\n{}".format(m.traceback.format_exc()))
            log("FLEX moved {} of {} (tool={})".format(moved, len(plan), mark.get("tool")))
        return ok

    RIGID_TOOLS = ("move", "rotate")
    FLEX_TOOLS = ("scale", "scale_axis", "extrude")

    def accept(mark):
        tool = mark.get("tool")

        # Axis Rotate predates the selection-time snapshot path. Keep its explicit
        # confirmation behavior until it has the same creation snapshot contract.
        if tool == "axis_rotate":
            primary = m._body.get(mark["id"])
            deps = []
            try:
                deps = detect_dependents(primary)
            except Exception:
                deps = []
            if deps:
                highlight(deps, True)
                take = confirm(len(deps))
                highlight(deps, False)
                if take:
                    return apply_together(rigid_matrix(mark), primary, deps)
            return old_accept(mark)

        if tool in RIGID_TOOLS:
            primary = m._body.get(mark["id"])
            design = m._design()
            scope = mark.get("move_scope", "only")
            related_handles = list(mark.get("related_tokens", []) or [])
            all_handles = list(mark.get("all_tokens_at_mark", []) or [])
            related_snapshot = resolve_token_bodies(design, related_handles)
            all_snapshot = resolve_token_bodies(design, all_handles)
            specs = []
            approved = 0
            built_on = 0
            try:
                current = detect_dependents(primary)
            except Exception:
                current = []

            for b in current:
                was_related = contains_body(related_snapshot, b)
                existed_at_mark = contains_body(all_snapshot, b)
                if was_related:
                    if scope == "together":
                        specs.append((b, body_token(b)))
                        approved += 1
                elif all_handles and not existed_at_mark:
                    # This body genuinely did not exist when the unresolved
                    # decision was created and is now attached to the upstream
                    # body. It is downstream work and must follow automatically.
                    specs.append((b, body_token(b)))
                    built_on += 1

            log("MOVE/ROTATE carry scope={} approved_neighbours={} built_on_top={}".format(
                scope, approved, built_on))
            ok = old_accept(mark)
            if ok and specs:
                moved = carry_bodies(rigid_matrix(mark), specs, design)
                log("carried {} of {} dependant bodies".format(moved, len(specs)))
            return ok

        if tool in FLEX_TOOLS:
            primary = m._body.get(mark["id"])

            if tool in ("scale", "scale_axis") and mark.get("scope_asked"):
                design = m._design()
                scope = mark.get("move_scope", "only")
                related_handles = list(mark.get("related_tokens", []) or [])
                all_handles = list(mark.get("all_tokens_at_mark", []) or [])
                related_snapshot = resolve_token_bodies(design, related_handles)
                all_snapshot = resolve_token_bodies(design, all_handles)
                try:
                    current = detect_flex_deps(mark, primary)
                except Exception:
                    current = []
                chosen = []
                attached = built_on = 0
                for b in current:
                    was_related = contains_body(related_snapshot, b)
                    existed_at_mark = contains_body(all_snapshot, b)
                    if was_related:
                        if scope == "together":
                            chosen.append(b)
                            attached += 1
                    elif all_handles and not existed_at_mark:
                        chosen.append(b)
                        built_on += 1
                log("SCALE carry scope={} attached={} built_on_top={}".format(
                    scope, attached, built_on))
                return apply_flex(mark, chosen)

            # Extrude (and any scale with no selection-time answer) keeps the
            # explicit confirmation path because no creation snapshot is available.
            deps = []
            try:
                deps = detect_flex_deps(mark, primary)
            except Exception:
                deps = []
            if deps:
                highlight(deps, True)
                take = confirm(len(deps))
                highlight(deps, False)
                if take:
                    return apply_flex(mark, deps)

        return old_accept(mark)

    m._accept = accept
    log("DEPENDENT FOLLOW READY (entity-resolved handoff identity + downstream carry)")
