#!/usr/bin/env bash
# Put test data uploaded with tools/testdata/pack.py where the tests look for it, in a Linux cloud session.
#
#   bash tools/testdata/link.sh <branch>     # fetch origin/<branch> and use its testdata/ folder
#   bash tools/testdata/link.sh <folder>     # use a folder that holds the packed manifest.json (copied)
#   bash tools/testdata/link.sh --remove     # undo everything below
#
# The tests name Windows paths (E:/Games/Exports/..., D:/<checkout>/out/edits/...). On Linux those are relative
# paths, resolved against the repository root, where tools/run_all_tests.py runs every suite. So the data is
# extracted to /opt/n2e-testdata (real files: tools resolve() paths and compare them), and each drive folder in it is
# linked from the repository root as "<letter>:" (kept out of git status through .git/info/exclude). The repository's
# own Windows path (manifest repo_root) is linked, inside that drive folder, to this checkout, and packed files of the
# repository's out/ are copied into out/ (never over an existing file) and listed, so --remove deletes only those.
set -euo pipefail

# N2E_LINK_ROOT / N2E_TESTDATA_DIR: another checkout and data folder (tools/test_portable.py uses throwaway ones).
ROOT="$(cd "${N2E_LINK_ROOT:-$(dirname "${BASH_SOURCE[0]}")/../..}" && pwd)"
DATA="${N2E_TESTDATA_DIR:-/opt/n2e-testdata}"
EXCLUDE="$(cd "$ROOT" && realpath -m "$(git rev-parse --git-path info/exclude)")"   # a worktree's .git is a file
COPIED="$DATA/.copied-into-repo"                 # repository files this script added, one relative path per line

remove() {
  if [ -f "$COPIED" ]; then
    while IFS= read -r rel; do
      rm -f "$ROOT/$rel"
      dir="$(dirname "$rel")"                    # the folders the copy needed, if now empty; nothing else in out/
      while [ "$dir" != "." ] && rmdir "$ROOT/$dir" 2>/dev/null; do dir="$(dirname "$dir")"; done
    done < "$COPIED"
  fi
  if [ -d "$ROOT/out" ]; then
    find "$ROOT/out" -type l -lname "$DATA/*" -delete   # links an earlier version of this script made
  fi
  for link in "$ROOT"/?:; do
    if [ -L "$link" ]; then rm "$link"; fi
  done
  rm -rf "$DATA"
  if [ -f "$EXCLUDE" ]; then sed -i '/^\/[A-Z]:$/d' "$EXCLUDE"; fi
  echo "test data removed"
}

if [ "${1:-}" = "--remove" ]; then
  remove
  exit 0
fi
if [ $# -ne 1 ]; then
  sed -n '2,7p' "$0"
  exit 2
fi

remove >/dev/null
mkdir -p "$DATA"
if [ -f "$1/manifest.json" ]; then
  cp -a "$1/." "$DATA/"
else
  git -C "$ROOT" fetch --depth 1 origin "$1"
  git -C "$ROOT" archive FETCH_HEAD testdata | tar -x -C "$DATA" --strip-components 1
fi
[ -f "$DATA/manifest.json" ] || { echo "no manifest.json in $1 (pack it with tools/testdata/pack.py)"; exit 1; }
: > "$COPIED"

link_drive() {                                    # "<letter>:" in the repository root -> $DATA/<letter>
  local letter="$1"
  mkdir -p "$DATA/$letter"
  ln -sfn "$DATA/$letter" "$ROOT/$letter:"
  mkdir -p "$(dirname "$EXCLUDE")"
  grep -qxF "/$letter:" "$EXCLUDE" 2>/dev/null || echo "/$letter:" >> "$EXCLUDE"
}

for dir in "$DATA"/?; do
  if [ -d "$dir" ]; then link_drive "$(basename "$dir")"; fi
done

# The repository's Windows path (e.g. D:\Work\Niagara2Effekseer) -> this checkout, so tests
# that name it literally find out/ here.
repo_root="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["repo_root"])' "$DATA/manifest.json")"
if [[ "$repo_root" =~ ^([A-Za-z]):[\\/](.*)$ ]]; then
  letter="${BASH_REMATCH[1]^^}"
  rest="${BASH_REMATCH[2]//\\//}"
  link_drive "$letter"
  if [ -e "$DATA/$letter/$rest" ] && [ ! -L "$DATA/$letter/$rest" ]; then
    echo "WARNING: $letter:/$rest holds packed files; not linking it to the checkout"
  else
    mkdir -p "$(dirname "$DATA/$letter/$rest")"
    ln -sfn "$ROOT" "$DATA/$letter/$rest"
  fi
fi

if [ -d "$DATA/_repo" ]; then                    # out/... only: pack.py packs nothing else from the repository
  (cd "$DATA/_repo" && find . -type f -printf '%P\n') | while IFS= read -r rel; do
    if [ -e "$ROOT/$rel" ] && [ ! -L "$ROOT/$rel" ]; then
      echo "kept the checkout's own $rel"
    else
      rm -f "$ROOT/$rel"                         # a link left by an earlier version of this script
      mkdir -p "$(dirname "$ROOT/$rel")"
      cp -a "$DATA/_repo/$rel" "$ROOT/$rel"
      echo "$rel" >> "$COPIED"
    fi
  done
fi

python3 - "$DATA/manifest.json" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))
size = sum(f['size'] for f in m['files'])
print('placed %d file(s), %.1f MB; %d too large and %d unplaced were not packed'
      % (len(m['files']), size / 1048576, len(m['too_large']), len(m['unplaced'])))
EOF
echo "drives: $(cd "$ROOT" && ls -d ?: 2>/dev/null | tr '\n' ' ')  (undo: bash tools/testdata/link.sh --remove)"
