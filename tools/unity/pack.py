"""pack: zip the Unity tools to run on Windows without the repo: unityfx.ps1 and the five .py files it runs (bundle
side), unity_rdc.py with run_rdc.bat and the tools/rdcdigest modules it needs in rdc_tools/ (capture side), and a
readme. Packed from the repo's current files every time, so the bundle's copies never drift from tools/.

  python tools/unity/pack.py [--out out/unity/unityfx-bundle.zip]
"""
import argparse
from pathlib import Path
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RDC = REPO / 'tools' / 'rdcdigest'
# unityfx.ps1 and the five .py files it checks for beside it
BUNDLE_SIDE = ('unityfx.ps1', 'fx_reference_index.py', 'unity_dump.py', 'shuriken_summary.py', 'shuriken.py',
               'unityasset.py')
# rdcdigest.py runs rd_digest.py (and rd_texpng.py) inside qrenderdoc; rd_digest imports rawdiff; the rest is imports
RDC_MODULES = ('rdcdigest.py', 'rd_digest.py', 'rd_texpng.py', 'rawdiff.py', 'ddsio.py', 'digestlib.py')
FILES = {name: HERE / name for name in BUNDLE_SIDE + ('unity_rdc.py', 'run_rdc.bat')}
FILES['README-unity.txt'] = HERE / 'BUNDLE-README.txt'
FILES.update({'rdc_tools/' + m: RDC / m for m in RDC_MODULES})
CRLF = ('.bat', '.txt', '.ps1')              # cmd.exe misreads labels in LF batch files


def version():
    def git(*a):
        return subprocess.run(['git', '-C', str(REPO), *a], capture_output=True, text=True).stdout.strip()
    dirty = git('status', '--porcelain', '--', 'tools/unity', 'tools/rdcdigest')
    return '%s%s\n' % (git('rev-parse', '--short', 'HEAD') or 'unknown',
                       ' + uncommitted changes in tools/unity or tools/rdcdigest' if dirty else '')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=str(REPO / 'out' / 'unity' / 'unityfx-bundle.zip'))
    out = Path(ap.parse_args().out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, src in FILES.items():
            data = src.read_bytes()
            if name.endswith(CRLF):
                data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            z.writestr(name, data)
        z.writestr('VERSION.txt', version().replace('\n', '\r\n'))
    print('wrote %s (%d files)' % (out, len(FILES) + 1))


if __name__ == '__main__':
    main()
