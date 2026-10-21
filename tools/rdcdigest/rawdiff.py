"""Pure-Python helpers rd_digest.py uses inside qrenderdoc (its Python has no numpy); tested by test_rdcdigest.py.

changed_box(a, b, w, h, bpp) -> (x, y, w, h) of the pixels whose bytes differ between two tightly packed images,
or None when they are equal. crop(data, w, bpp, box) -> the rows of box, tightly packed."""


def _first_diff(a, b, lo, hi):
    """Smallest i in [lo, hi) with a[i] != b[i]; the caller knows one exists. Bisection on slice equality (memcmp)."""
    while hi - lo > 32:
        mid = (lo + hi) // 2
        if a[lo:mid] != b[lo:mid]:
            hi = mid
        else:
            lo = mid
    for i in range(lo, hi):
        if a[i] != b[i]:
            return i
    return hi


def _last_diff(a, b, lo, hi):
    """Largest i in [lo, hi) with a[i] != b[i]; the caller knows one exists."""
    while hi - lo > 32:
        mid = (lo + hi) // 2
        if a[mid:hi] != b[mid:hi]:
            lo = mid
        else:
            hi = mid
    for i in range(hi - 1, lo - 1, -1):
        if a[i] != b[i]:
            return i
    return lo


def changed_box(a, b, w, h, bpp):
    if a == b:
        return None
    rb = w * bpp
    rows = [y for y in range(h) if a[y * rb:(y + 1) * rb] != b[y * rb:(y + 1) * rb]]
    x0, x1 = w, -1
    for y in rows:
        o = y * rb
        ra, rbb = a[o:o + rb], b[o:o + rb]
        if x0 > 0 and ra[:x0 * bpp] != rbb[:x0 * bpp]:
            x0 = _first_diff(ra, rbb, 0, x0 * bpp) // bpp
        if x1 < w - 1 and ra[(x1 + 1) * bpp:] != rbb[(x1 + 1) * bpp:]:
            x1 = _last_diff(ra, rbb, (x1 + 1) * bpp, rb) // bpp
    return (x0, rows[0], x1 - x0 + 1, rows[-1] - rows[0] + 1)


def crop(data, w, bpp, box):
    x, y, cw, ch = box
    rb = w * bpp
    return b''.join(data[(y + r) * rb + x * bpp:(y + r) * rb + (x + cw) * bpp] for r in range(ch))
