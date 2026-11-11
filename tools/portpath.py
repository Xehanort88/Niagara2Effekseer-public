"""Paths that behave the same on the user's Windows machine and in a Linux cloud session.

The tests and the data name Windows paths (E:/Games/Exports/..., D:\\<checkout>\\out\\edits\\...). On Linux such a
path is relative to the working folder, where tools/testdata/link.sh links the uploaded data under the same names
(README § Test data for a cloud session). Three things still differ, and these helpers cover them:

- split_paths: folder lists are joined with os.pathsep, which is ':' on Linux, the character every drive path holds;
- native: a path stored inside a data file with backslashes is one long file name on Linux;
- find: Windows ignores letter case in file names and Linux does not, so an exported file whose case differs from
  the reference to it is found on Windows only.
On Windows each helper returns what the plain code did before.
"""
import os
import re
from pathlib import Path

_DRIVE = re.compile(r'^[A-Za-z]:[\\/]')
_LISTINGS = {}


def split_paths(value):
    """The non-empty folders of an os.pathsep-joined list. Where the separator is ':', a part ending in a lone drive
    letter (all of it, or its last folder: '<repo>/E' when a manifest made 'E:/a' relative to the repository) followed by
    a part starting with / or \\ is a Windows drive path cut in two, and is joined back ('E:/a:/b' -> ['E:/a', '/b']),
    unless only the two separate parts exist."""
    parts = (value or '').split(os.pathsep)
    if os.pathsep != ':':
        return [p for p in parts if p]
    out, i = [], 0
    while i < len(parts):
        part = parts[i]
        letter = part.replace('\\', '/').rsplit('/', 1)[-1]
        if len(letter) == 1 and letter.isalpha() and i + 1 < len(parts) and parts[i + 1][:1] in ('/', '\\'):
            joined = part + ':' + parts[i + 1]
            if len(part) == 1 or os.path.exists(joined) or not (os.path.exists(part) and os.path.exists(parts[i + 1])):
                part, i = joined, i + 1
        if part:
            out.append(part)
        i += 1
    return out


def under(root, path):
    """A path read from a data file (e.g. a shader index's HLSLPath), joined to `root` when it is relative. A drive
    path counts as absolute everywhere: on Linux 'E:/...' is resolved from the working folder, where the test data is
    linked. With no `root`, the path alone. On Windows this is `Path(p) if absolute else root / p`, as before."""
    path = native(path)
    if not root or Path(path).is_absolute() or (isinstance(path, str) and _DRIVE.match(path)):
        return Path(path)
    return Path(root) / path


def for_subprocess(value):
    """A JSON value (an `edit` spec) about to be read by a program that resolves relative paths against its own folder:
    dotnet `edit` resolves them against the spec's. Off Windows, every Windows drive path in it ('E:/...', relative
    there) becomes absolute, resolved from the working folder as Python resolved it when it found the file. A new
    value is returned; on Windows the value itself, unchanged."""
    if os.sep != '/':
        return value
    if isinstance(value, str):
        return os.path.abspath(native(value)) if _DRIVE.match(value) else value
    if isinstance(value, dict):
        return {k: for_subprocess(v) for k, v in value.items()}
    if isinstance(value, list):
        return [for_subprocess(v) for v in value]
    return value


def native(path):
    """A path read from a data file, usable here: on Linux a Windows drive path gets forward slashes. Anything else,
    and every path on Windows, comes back unchanged (None too)."""
    if os.sep == '/' and isinstance(path, str) and _DRIVE.match(path):
        return path.replace('\\', '/')
    return path


def _names(folder):
    """{name.casefold(): name} of a folder's entries, read once per folder."""
    key = str(folder)
    if key not in _LISTINGS:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            names = []
        listing = {}
        for name in names:
            listing.setdefault(name.casefold(), name)
        _LISTINGS[key] = listing
    return _LISTINGS[key]


def find(path):
    """`path` as a Path when it exists; otherwise, where file names are case-sensitive, the existing path that differs
    from it only in letter case, component by component; else None. On Windows: the path if it exists, else None."""
    p = Path(path)
    if p.exists():
        return p
    if os.name == 'nt':
        return None
    parts = p.parts
    current = Path(parts[0]) if p.is_absolute() else Path('.')
    for name in parts[1:] if p.is_absolute() else parts:
        exact = current / name
        if exact.exists():
            current = exact
            continue
        hit = _names(current).get(name.casefold())
        if hit is None:
            return None
        current = current / hit
    return current if p.is_absolute() else Path(os.path.normpath(current))
