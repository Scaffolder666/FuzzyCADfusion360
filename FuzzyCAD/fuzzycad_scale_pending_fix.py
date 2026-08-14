"""Fix Uniform Scale selection and preserve the direct scale interaction.

The legacy pending-builder only knew transform/extrude/fillet, so after Scale
was split into its own command a perfectly valid BRepBody still returned None
and Fusion showed "can't use that selection for 'scale'".  This patch adds the
missing Scale pending state while leaving every other command unchanged.
"""


def install(m):
    adsk = m.adsk
    old_build_pending = m._build_pending
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
            (m._app or adsk.core.Application.get()).log("[FuzzyCAD SCALE] " + msg)
        except Exception:
            pass

    def build_pending(cmd, ent):
        if cmd == "scale":
            if not isinstance(ent, adsk.fusion.BRepBody):
                return None
            center, size = m._bbox_center_size(ent)
            bb = ent.boundingBox
            dx = bb.maxPoint.x - bb.minPoint.x
            dy = bb.maxPoint.y - bb.minPoint.y
            dz = bb.maxPoint.z - bb.minPoint.z
            radial = max((dx * dx + dy * dy + dz * dz) ** 0.5 * 0.5, 1e-6)
            return {
                "geom": {"edges": m._sample_edges(ent.edges)},
                "anchor": center,
                "size": size,
                "entity": ent,
                "body": ent,
                "scale_len": radial,
            }
        return old_build_pending(cmd, ent)

    m._build_pending = build_pending

    def run(context):
        result = old_run(context)
        log("UNIFORM SCALE SELECTION FIX READY: BRepBody now builds a scale pending state")
        return result

    m.run = run
