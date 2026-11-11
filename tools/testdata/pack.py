"""Turn the traces of `tools/run_all_tests.py --trace-files` into a folder to upload, so the same suites can run in a
cloud session (tools/testdata/link.sh puts the files back where the tests look for them).

    python tools/run_all_tests.py --work out/tests/trace --trace-files --only cascade-bd2,cascade-ot2port
    python tools/testdata/pack.py --traces out/tests/trace/traces --out testdata --dry-run
    python tools/testdata/pack.py --traces out/tests/trace/traces --out testdata

Every file a suite read or checked is copied to <out>/<drive letter>/<rest of its path> (E:/a/b.json -> E/a/b.json), or, when it
is inside this repository (its out/ folder), to <out>/_repo/<path in the repository>. Files a trace names that no
longer exist are left out (existence probes). Files over --max-mb (default 95: GitHub refuses files over 100 MB) are
not copied but listed. Folders the suites only listed are reported with their direct file count and size, not copied:
add one with --listing <folder> when a test counts a folder's files. <out>/manifest.json and <out>/REPORT.md say what
was packed for which suite.
"""
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[2]
DRIVE = re.compile(r'^([A-Za-z]):[\\/]')
SHOWN_LISTINGS = 40                                   # REPORT.md rows; manifest.json keeps every listed folder
ARGDIR_MB = 50                                        # a folder passed to dotnet (--meshes): its files, up to this


def packed_path(path, repo):
    """Where `path` goes inside the upload folder, as a POSIX relative path; None when it cannot be placed."""
    text = str(path)
    if os.path.normcase(text).startswith(os.path.normcase(str(repo)) + os.sep):
        return '_repo/' + Path(text).relative_to(repo).as_posix()
    m = DRIVE.match(text)
    if m:
        return m.group(1).upper() + '/' + PureWindowsPath(text[3:]).as_posix()
    if text.startswith('/'):
        return '_root' + Path(text).as_posix()
    return None


def read_traces(folder):
    """{(kind, path): set of suites} from every <suite>.tsv."""
    seen = {}
    for trace in sorted(Path(folder).glob('*.tsv')):
        for line in trace.read_text(encoding='utf-8', errors='surrogateescape').splitlines():
            kind, _, path = line.partition('\t')
            if path:
                seen.setdefault((kind, path), set()).add(trace.stem)
    return seen


def direct_files(folder):
    try:
        return [e for e in os.scandir(folder) if e.is_file()]
    except OSError:
        return []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--traces', type=Path, required=True, help='<work>/traces of a --trace-files run')
    ap.add_argument('--out', type=Path, required=True, help='upload folder to fill (e.g. testdata)')
    ap.add_argument('--max-mb', type=float, default=95, help='largest file to copy, MB (default 95)')
    ap.add_argument('--listing', type=Path, action='append', default=[],
                    help='also copy every file directly in this listed folder (repeatable)')
    ap.add_argument('--dry-run', action='store_true', help='report only, copy nothing')
    args = ap.parse_args()

    seen = read_traces(args.traces)
    if not seen:
        sys.exit('no trace lines under %s (run tools/run_all_tests.py --trace-files first)' % args.traces)
    limit = int(args.max_mb * 1024 * 1024)
    files, listed, large, unplaced, argdirs = {}, {}, [], [], []
    for (kind, path), suites in sorted(seen.items()):
        if kind == 'argdir' and os.path.isdir(path):
            # dotnet reads what it needs from it (a --meshes folder): its own files, when that is small enough
            entries = direct_files(path)
            size = sum(e.stat().st_size for e in entries)
            argdirs.append({'path': path, 'files': len(entries), 'size': size, 'suites': sorted(suites),
                            'packed': size <= ARGDIR_MB * 1048576})
            if size <= ARGDIR_MB * 1048576:
                for entry in entries:
                    files.setdefault(entry.path, set()).update(suites)
            listed.setdefault(path, set()).update(suites)
        elif kind == 'list' or (kind == 'arg' and os.path.isdir(path)):
            listed.setdefault(path, set()).update(suites)
        elif kind in ('read', 'stat', 'arg') and os.path.isfile(path):
            files.setdefault(path, set()).update(suites)
    for folder in args.listing:
        for entry in direct_files(folder):
            files.setdefault(entry.path, set()).add('--listing')

    rows = []
    for path, suites in sorted(files.items()):
        target = packed_path(path, ROOT)
        size = os.path.getsize(path)
        row = {'path': path, 'packed': target, 'size': size, 'suites': sorted(suites)}
        if target is None:
            unplaced.append(row)
        elif size > limit:
            large.append(row)
        else:
            rows.append(row)
    listings = []
    for folder, suites in sorted(listed.items()):
        entries = direct_files(folder)
        listings.append({'path': folder, 'files': len(entries), 'size': sum(e.stat().st_size for e in entries),
                         'suites': sorted(suites)})
    listings.sort(key=lambda l: -l['files'])            # an rglob lists thousands of folders: largest first

    if not args.dry_run:
        for row in rows:
            dest = args.out / row['packed']
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(row['path'], dest)
        args.out.mkdir(parents=True, exist_ok=True)
        manifest = {'repo_root': str(ROOT), 'files': rows, 'too_large': large, 'unplaced': unplaced,
                    'listed_folders': listings, 'subprocess_folders': argdirs}
        (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')

    def mb(n):
        return '%.1f MB' % (n / 1048576)

    per_suite = {}
    for row in rows:
        for s in row['suites']:
            n, b = per_suite.get(s, (0, 0))
            per_suite[s] = (n + 1, b + row['size'])
    lines = ['# Test data upload', '', 'Repository root when traced: `%s`' % ROOT, '',
             '%d file(s), %s to upload%s.' % (len(rows), mb(sum(r['size'] for r in rows)),
                                             ' (dry run: nothing copied)' if args.dry_run else ''), '',
             '| Suite | Files | Size |', '|---|---|---|']
    lines += ['| %s | %d | %s |' % (s, n, mb(b)) for s, (n, b) in sorted(per_suite.items())]
    if large:
        lines += ['', '## Not copied: over %s MB (GitHub refuses files over 100 MB)' % args.max_mb, '']
        lines += ['- `%s` (%s; %s)' % (r['path'], mb(r['size']), ', '.join(r['suites'])) for r in large]
    skipped = [d for d in argdirs if not d['packed']]
    if skipped:
        lines += ['', '## Folders passed to dotnet, not copied: over %d MB' % ARGDIR_MB, '']
        lines += ['- `%s` (%d files, %s; %s)' % (d['path'], d['files'], mb(d['size']), ', '.join(d['suites']))
                  for d in skipped]
    if unplaced:
        lines += ['', '## Not copied: path cannot be placed', '']
        lines += ['- `%s`' % r['path'] for r in unplaced]
    if listings:
        lines += ['', '## Folders only listed (not copied; add with --listing if a test counts their files)', '',
                  '| Folder | Files directly in it | Size | Suites |', '|---|---|---|---|']
        lines += ['| `%s` | %d | %s | %s |' % (l['path'], l['files'], mb(l['size']), ', '.join(l['suites']))
                  for l in listings[:SHOWN_LISTINGS]]
        if len(listings) > SHOWN_LISTINGS:
            lines.append('\n%d more listed folder(s) in manifest.json.' % (len(listings) - SHOWN_LISTINGS))
    report = '\n'.join(lines) + '\n'
    if not args.dry_run:
        (args.out / 'REPORT.md').write_text(report, encoding='utf-8')
    print(report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
