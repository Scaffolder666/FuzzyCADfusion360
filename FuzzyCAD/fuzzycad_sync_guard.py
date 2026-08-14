"""Defensive guards for transient FuzzyCAD live-mark state.

Fusion can deliver a final executePreview event after a mark has already been
removed during command teardown. In that case _live can still contain the old
mark id for a few milliseconds even though _marks no longer contains it. The
legacy implementation dereferenced _find(id) unconditionally and crashed.
"""


def install(m):
    def sync_category(cat):
        """Create/update a live mark, but tolerate a stale _live id."""
        op = m._category_raw(cat)
        mid = m._live.get(cat)

        if mid is not None:
            mark = m._find(mid)
            if mark is None:
                # A preview event raced command teardown/removal. Do not recreate
                # the mark from the old non-zero manipulator value; the next fresh
                # command will reset _live and can create a new proposal normally.
                return None
            mark.update(op)
            return mark

        if m._is_default(cat, op):
            return None

        mid = m._next_id
        m._next_id += 1
        mark = m._make_mark(cat, op)
        mark["id"] = mid
        m._geom[mid] = m._pending["geom"]
        m._entity[mid] = m._pending["entity"]
        m._body[mid] = m._pending["body"]
        m._marks.append(mark)
        m._live[cat] = mid
        m._send_state()
        return mark

    def seed_single(cat, amount):
        """Seed extrude/fillet safely if a stale live id survives teardown."""
        mid = m._live.get(cat)
        if mid is not None:
            mark = m._find(mid)
            if mark is None:
                return None
            mark["amount"] = amount
            if cat == "fillet":
                mark["last_valid_amount"] = amount
            return mark

        mid = m._next_id
        m._next_id += 1
        mark = m._make_mark(cat, {"amount": amount})
        mark["id"] = mid
        m._geom[mid] = m._pending["geom"]
        m._entity[mid] = m._pending["entity"]
        m._body[mid] = m._pending["body"]
        m._marks.append(mark)
        m._live[cat] = mid
        if cat == "fillet":
            mark["last_valid_amount"] = amount
        m._send_state()
        return mark

    m._sync_category = sync_category
    m._seed_single = seed_single
