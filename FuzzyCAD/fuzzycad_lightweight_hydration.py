"""Keep persistence hydration display-only.

Persisted Extrude/Fillet rows can be reconstructed from their saved references
and lightweight proposal geometry. Building and deleting real Fusion features
while a document/add-in is still opening is unnecessary and is a high-risk time
to touch the modeling kernel. Exact candidates are recomputed later at explicit
user settle points.
"""


def install(m):
    old_run = m.run
    old_reload = getattr(m, "_reload_persisted_state", None)

    def without_exact(fn, *args, **kwargs):
        real_compute = getattr(m, "_compute_real", None)
        if real_compute is None:
            return fn(*args, **kwargs)
        m._compute_real = lambda mark: False
        try:
            return fn(*args, **kwargs)
        finally:
            m._compute_real = real_compute

    if old_reload is not None:
        def reload_state(*args, **kwargs):
            return without_exact(old_reload, *args, **kwargs)
        m._reload_persisted_state = reload_state

    def run(context):
        return without_exact(old_run, context)

    m.run = run
