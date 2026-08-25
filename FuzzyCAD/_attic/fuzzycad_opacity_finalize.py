"""Reassert the exact opacity manager after legacy group-ghost wrappers install.

The robust opacity manager already includes Move-Together related bodies, so the
older proposal-group opacity wrapper is redundant and can otherwise restore a
related body to the temporary ghost value instead of its true original opacity.
"""


def install(m):
    refresh = getattr(m, "_opacity_refresh_ghost", None)
    restore = getattr(m, "_opacity_restore_all_bodies", None)
    if refresh is not None:
        m._refresh_ghost = refresh
    if restore is not None:
        m._restore_all_bodies = restore
