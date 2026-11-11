"""Records which files a Python process reads from outside the repository (tools/run_all_tests.py --trace-files).

Python imports this module at startup when its folder is on PYTHONPATH; the runner puts it there for each suite, so
every Python process the suite starts (builders, oracles, tool subprocesses) is traced too. Nothing happens unless
N2E_TRACE_FILE is set. Each line of that file is "<kind>\t<absolute path>":
    read    a file opened for reading, or copied with shutil (Windows copies without opening it in Python)
    stat    an existing file only checked (Path.exists/is_file, os.path.isfile...): tests check inputs before use,
            and some are then read by dotnet, which this hook does not see
    list    a directory listed (os.listdir, os.scandir: Path.glob/rglob/iterdir use these)
    glob    a glob.glob pattern
    arg     an existing file passed to a subprocess (dotnet, node read it themselves), on its command line or inside
            a JSON file on it (an `edit` spec names its textures and models)
    argdir  an existing folder passed the same way (a --meshes folder: the files directly in it are packed)
Left out: paths under N2E_TRACE_IGNORE (os.pathsep-separated: the suite's --work, the temp folder) and Python's own
install, always; paths under N2E_TRACE_REPO (the repository) unless they are under N2E_TRACE_KEEP (its out/, which
holds external data such as the catalogue and other tasks' outputs).
"""
import _thread
import os
import shlex
import stat as _stat
import sys

_TRACE = os.environ.get('N2E_TRACE_FILE')
_SPEC_LIMIT = 5 * 1024 * 1024                      # JSON files on a command line read for paths, at most this size


def _split_command_line(argv):
    """Arguments of a command line as subprocess.Popen's audit event gives it, without the program itself (python,
    dotnet, cmd.exe are not test data). On Windows the event comes after list2cmdline, so a list arrives as one
    string: split it back (quoted arguments keep spaces)."""
    if isinstance(argv, (str, bytes, os.PathLike)):
        if os.name == 'nt' and isinstance(argv, str) and not os.path.exists(argv):
            try:
                parts = shlex.split(argv, posix=False)
            except ValueError:                      # an argument with an unmatched quote: plain words then
                parts = argv.split()
            argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in parts]
        else:
            return []
    return list(argv or [])[1:]


def _folders(variable):
    return tuple(os.path.normcase(os.path.abspath(p)).rstrip('\\/') + os.sep
                 for p in os.environ.get(variable, '').split(os.pathsep) if p)


if _TRACE:
    _fd = os.open(_TRACE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)    # opened before the hook: not traced
    _skip = _folders('N2E_TRACE_IGNORE') + tuple(
        os.path.normcase(os.path.abspath(p)).rstrip('\\/') + os.sep
        for p in {sys.prefix, sys.base_prefix, sys.exec_prefix} | {p for p in sys.path if 'site-packages' in p})
    _repo = _folders('N2E_TRACE_REPO')
    _keep = _folders('N2E_TRACE_KEEP')
    _writes = os.O_WRONLY | os.O_RDWR
    _real_stat = os.stat
    # Programs found on PATH (shutil.which checks each candidate) are tools, not test data.
    _path_dirs = {os.path.normcase(os.path.abspath(d)) for d in os.environ.get('PATH', '').split(os.pathsep) if d}
    _inside = set()          # threads now recording: what recording itself opens, stats and parses is not traced

    def _guarded(step, *args):
        """Run one recording step unless this thread is recording already. A failure never reaches the traced
        program: an exception raised in an audit hook would abort the call being audited (subprocess.Popen...)."""
        me = _thread.get_ident()
        if me in _inside:
            return
        _inside.add(me)
        try:
            step(*args)
        except Exception:
            pass
        finally:
            _inside.discard(me)

    def _kind_of(path):
        try:
            mode = _real_stat(path).st_mode
        except (OSError, ValueError, TypeError):
            return None
        return 'dir' if _stat.S_ISDIR(mode) else 'file' if _stat.S_ISREG(mode) else None

    def _record(kind, path):
        try:
            full = os.path.abspath(os.fsdecode(path))              # keep the case: Linux needs the exact name later
        except (TypeError, ValueError):
            return
        folded = os.path.normcase(full) + (os.sep if _kind_of(full) == 'dir' else '')   # a listed folder itself
        if folded.startswith(_skip) or (folded.startswith(_repo) and not folded.startswith(_keep)):
            return
        os.write(_fd, ('%s\t%s\n' % (kind, full)).encode('utf-8', 'surrogateescape'))

    def _strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from _strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from _strings(v)

    def _subprocess_paths(argv):
        import json
        for arg in _split_command_line(argv):
            if not isinstance(arg, (str, os.PathLike)):
                continue
            kind = _kind_of(arg)
            if kind:
                _record('arg' if kind == 'file' else 'argdir', arg)
            if kind == 'file' and str(arg).lower().endswith('.json') and _real_stat(arg).st_size <= _SPEC_LIMIT:
                try:
                    with open(arg, encoding='utf-8-sig') as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                for s in _strings(data):
                    if ('/' in s or '\\' in s) and len(s) < 1024:
                        found = _kind_of(s)
                        if found:
                            _record('arg' if found == 'file' else 'argdir', s)

    def _on_event(event, args):
        if event == 'open':
            path, mode, flags = args
            if path is None or isinstance(path, int):
                return
            if mode is not None:
                if any(c in mode for c in 'wax'):
                    return
            elif flags & _writes:                                     # os.open passes flags, not a mode
                return
            _record('read', path)
        elif event in ('os.listdir', 'os.scandir'):
            path = args[0] if args else None
            if not isinstance(path, int):
                _record('list', '.' if path is None else path)
        elif event == 'glob.glob':
            _record('glob', args[0])
        elif event == 'shutil.copyfile':              # Windows copies with CopyFile2: no 'open' event (copytree too)
            _record('read', args[0])
        elif event == 'subprocess.Popen':
            _subprocess_paths(args[1])

    def _hook(event, args):
        _guarded(_on_event, event, args)

    def _record_check(path):
        """An existing file a program only checked; not one found on PATH."""
        if os.path.normcase(os.path.dirname(os.path.abspath(os.fsdecode(path)))) not in _path_dirs \
                and _kind_of(path) == 'file':
            _record('stat', path)

    def _traced_stat(path, *args, **kwargs):
        result = _real_stat(path, *args, **kwargs)
        if not isinstance(path, int) and kwargs.get('dir_fd') is None and _stat.S_ISREG(result.st_mode):
            _guarded(_record_check, path)
        return result

    def _traced_check(check):
        """os.path.exists / isfile: on Windows (Python 3.12+) these are C functions that skip os.stat, and pathlib's
        exists()/is_file() call them (3.13), so they are wrapped as well."""
        def traced(path):
            found = check(path)
            if found and not isinstance(path, int):
                _guarded(_record_check, path)
            return found
        return traced

    if os.name == 'nt':
        import _winapi
        if hasattr(_winapi, 'CopyFile2'):
            _real_copy = _winapi.CopyFile2

            def _traced_copy(src, *args, **kwargs):
                """shutil.copy2 (so copytree too) copies with CopyFile2 on Windows (Python 3.12+), with neither an
                'open' nor a 'shutil.copyfile' event; it looks the function up at each call."""
                _guarded(_record, 'read', src)
                return _real_copy(src, *args, **kwargs)
            _winapi.CopyFile2 = _traced_copy

    # The stdlib asks `os.stat in os.supports_follow_symlinks` (shutil.copystat, rmtree): keep the wrapper in each set
    # the real function is in, or those take another code path.
    for _name in ('supports_fd', 'supports_dir_fd', 'supports_follow_symlinks', 'supports_effective_ids'):
        _caps = getattr(os, _name, None)
        if isinstance(_caps, set) and _real_stat in _caps:
            _caps.add(_traced_stat)
    os.stat = _traced_stat                          # pathlib and os.path look these up at each call
    os.path.exists = _traced_check(os.path.exists)
    os.path.isfile = _traced_check(os.path.isfile)
    sys.addaudithook(_hook)
