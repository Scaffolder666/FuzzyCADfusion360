"""Selection-time scope question for the Scale command, mirroring Move scope.

Scale is non-rigid, so "following" means a touching neighbour TRANSLATES to stay
attached to the surface it sits on -- it is never resized (a bracket shouldn't
grow just because the plate grew). As with Move, the coupling decision is made
ONCE, up front: when a body is picked for Scale, the parts touching it are
detected, highlighted orange, and one question asks whether they stay attached.
The per-body displacement itself is computed later, at accept (see the FLEX path
in fuzzycad_dependent_follow), from whatever direction/factor the reviewer ends
up using -- so "stay attached?" is a structural yes/no here, and the magnitude
is mechanical. Fixed-side neighbours of a directional scale get a zero
displacement and stay put on their own.

Loaded after fuzzycad_dependent_follow so m._follow_detect_dependents (the shared
touching-set detector) is available; the snapshot it takes here is what accept
measures "built on top since" against.
"""


def install(m):
    adsk = m.adsk
    CurrentInputChanged = m.FuzzyInputChanged
    CurrentPreview = m.FuzzyPreview
    old_run = m.run

    HILITE_RGB = (225, 126, 38)

    def log(msg):
        try:
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD SCALE SCOPE] " + msg)
        except Exception:
            pass

    def body_token(b):
        try:
            return b.entityToken
        except Exception:
            return str(id(b))

    def highlight_related(related):
        grp = m._group(m.GROUP_PREVIEW)
        if grp is None:
            return
        for b in related:
            try:
                cg = grp.addBRepBody(b)
                cg.color = m._solid(HILITE_RGB)
                cg.setOpacity(0.45, True)
            except Exception:
                continue
        try:
            m._app.activeViewport.refresh()
        except Exception:
            pass

    def ask_scope(count):
        """Yes -> touching parts translate to stay attached; No -> only this part."""
        try:
            res = m._ui.messageBox(
                "{} adjacent part{} touching this one (highlighted in orange).\n\n"
                "Keep them attached as this part scales?".format(
                    count, " is" if count == 1 else "s are"),
                "FuzzyCAD — scale scope",
                adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                adsk.core.MessageBoxIconTypes.QuestionIconType)
            return "together" if res == adsk.core.DialogResults.DialogYes else "only"
        except Exception:
            return "only"

    def stamp_live_marks():
        """Copy the pending scope snapshot onto whichever scale mark is live, so it
        survives to accept (the mark, not m._pending, is the durable carrier)."""
        if not m._pending or not m._pending.get("scope_asked_for"):
            return
        rel_tok = list(m._pending.get("related_tokens", []))
        all_tok = list(m._pending.get("all_tokens", []))
        scope = m._pending.get("move_scope", "only")
        for key in ("scale", "scale_axis"):
            mid = m._live.get(key)
            mark = m._find(mid) if mid is not None else None
            if mark is not None:
                mark["related_tokens"] = rel_tok
                mark["all_tokens_at_mark"] = all_tok
                mark["move_scope"] = scope
                mark["scope_asked"] = True

    class FuzzyInputChanged(CurrentInputChanged):
        def notify(self, args):
            cid = None
            try:
                cid = args.input.id
            except Exception:
                pass
            super().notify(args)
            if getattr(m, "_active_cmd", None) != "scale":
                return
            try:
                primary = m._pending.get("body") if m._pending else None
                ptok = body_token(primary) if primary is not None else None
                # Ask the scope question once per selected body, at selection time.
                if (cid == "sel" and m._pending
                        and m._pending.get("scope_asked_for") != ptok):
                    detect = getattr(m, "_follow_detect_dependents", None)
                    try:
                        related = detect(primary) if detect else []
                    except Exception:
                        related = []
                    m._pending["related_bodies"] = list(related)
                    m._pending["related_tokens"] = [body_token(b) for b in related]
                    snap = getattr(m, "_follow_all_tokens", None)
                    m._pending["all_tokens"] = list(snap()) if snap else []
                    m._pending["scope_asked_for"] = ptok
                    if related:
                        highlight_related(related)
                        m._pending["move_scope"] = ask_scope(len(related))
                        log("SCOPE picked={} for {} touching part(s)".format(
                            m._pending["move_scope"], len(related)))
                    else:
                        m._pending["move_scope"] = "only"
                # Stamp on EVERY scale inputChanged: the handle drag (which creates
                # /updates the live scale mark) is delivered as inputChanged, not
                # executePreview, so this is where the scope snapshot reaches the mark.
                stamp_live_marks()
            except Exception:
                log("scale sel scope failed\n{}".format(m.traceback.format_exc()))

    m.FuzzyInputChanged = FuzzyInputChanged

    class FuzzyPreview(CurrentPreview):
        def notify(self, args):
            super().notify(args)
            try:
                if getattr(m, "_active_cmd", None) == "scale":
                    stamp_live_marks()
            except Exception:
                pass

    m.FuzzyPreview = FuzzyPreview

    def run(context):
        result = old_run(context)
        log("SCALE SCOPE READY: touching set asked once at selection, applied silently at accept")
        return result

    m.run = run
