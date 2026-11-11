#!/usr/bin/env bash
# Set up a fresh Linux sandbox or cloud container to build and test this repository (AGENTS.md § Environment setup):
# submodules, the .NET 9 SDK, the Python modules the test suites use, and both tool builds.
#
#   bash tools/setup/cloud_setup.sh
#
# Safe to run again: each step is skipped when already done. Environment:
#   DOTNET_DIR=<dir>   where the SDK goes when none is on PATH (default /opt/dotnet)
#   FORCE_DOTNET=1     install the SDK into DOTNET_DIR even when a .NET 9 SDK is already on PATH
#   SKIP_BUILD=1       stop before the two dotnet builds
# Hosts used: github.com (submodules), builds.dotnet.microsoft.com (Microsoft's installer, tried first),
# conda.anaconda.org (the fallback, pinned and checksummed below), pypi.org, api.nuget.org (the builds).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOTNET_DIR="${DOTNET_DIR:-/opt/dotnet}"
CONDA_URL="https://conda.anaconda.org/conda-forge/linux-64"
# conda-forge's build of the SDK and the runtime it depends on; sha256 from the channel's repodata.json.
CONDA_PACKAGES=(
  "dotnet-runtime-9.0.20-h7148c6a_0.conda af7241a64165aadb52049602ec04ab0391661f5d7449e0ef5e92a3be1bb08f09"
  "dotnet-sdk-9.0.318-h7148c6a_0.conda f5f3218c1cc2a91dd104aeb30ec2502f48651fa1461ce3ad4dd49201f4bccc4a"
)
PY_MODULES=("numpy:numpy" "PIL:Pillow" "yaml:PyYAML" "fontTools:fonttools" "zstandard:zstandard")  # import:package

say() { printf '== %s\n' "$*"; }
has_sdk9() { command -v dotnet >/dev/null 2>&1 && dotnet --list-sdks 2>/dev/null | grep -q '^9\.'; }

say "submodules"
git -C "$ROOT" submodule update --init --depth 1

say "python modules"
missing=()
for pair in "${PY_MODULES[@]}"; do
  python3 -c "import ${pair%%:*}" 2>/dev/null || missing+=("${pair#*:}")
done
if ((${#missing[@]})); then
  # A distribution's own Python refuses plain pip installs (PEP 668); a throwaway container may override that.
  python3 -m pip install -q "${missing[@]}" ||
    python3 -m pip install -q --user --break-system-packages "${missing[@]}"
else
  echo "all present"
fi

install_microsoft() {
  local script
  script="$(mktemp)"
  curl -fsSL https://dot.net/v1/dotnet-install.sh -o "$script" &&
    bash "$script" --channel 9.0 --install-dir "$DOTNET_DIR" --no-path
  local status=$?
  rm -f "$script"
  return $status
}

install_conda() {
  local tmp entry name sum
  tmp="$(mktemp -d)"
  for entry in "${CONDA_PACKAGES[@]}"; do
    name="${entry%% *}"; sum="${entry##* }"
    curl -fsSL "$CONDA_URL/$name" -o "$tmp/$name"
    echo "$sum  $tmp/$name" | sha256sum -c --quiet -
    # A .conda file is a zip holding pkg-*.tar.zst; the SDK lands under lib/dotnet.
    python3 - "$tmp/$name" "$tmp/prefix" <<'EOF'
import sys, tarfile, zipfile, zstandard
archive, prefix = sys.argv[1:]
with zipfile.ZipFile(archive) as z:
    for member in z.namelist():
        if member.startswith('pkg-') and member.endswith('.tar.zst'):
            with z.open(member) as f:
                tar = tarfile.open(fileobj=zstandard.ZstdDecompressor().stream_reader(f), mode='r|')
                if hasattr(tarfile, 'tar_filter'):          # Python 3.12+: refuse absolute paths and ../ members
                    tar.extractall(prefix, filter='tar')
                else:
                    tar.extractall(prefix)
EOF
  done
  mkdir -p "$DOTNET_DIR"
  cp -a "$tmp/prefix/lib/dotnet/." "$DOTNET_DIR/"
  rm -rf "$tmp"
}

say ".NET 9 SDK"
if has_sdk9 && [[ "${FORCE_DOTNET:-}" != 1 ]]; then
  echo "already on PATH: $(dotnet --version)"
else
  if install_microsoft; then
    echo "installed with Microsoft's dotnet-install.sh"
  else
    echo "Microsoft's installer is unreachable here; using the pinned conda-forge packages"
    install_conda
  fi
  "$DOTNET_DIR/dotnet" --version
  if [[ -w /usr/local/bin ]]; then
    ln -sf "$DOTNET_DIR/dotnet" /usr/local/bin/dotnet
  else
    echo "add $DOTNET_DIR to PATH (/usr/local/bin is not writable)"
  fi
  line="export DOTNET_ROOT=$DOTNET_DIR"
  grep -qxF "$line" ~/.bashrc 2>/dev/null || echo "$line" >> ~/.bashrc
  if [[ -n "${CLAUDE_ENV_FILE:-}" ]]; then          # set when run from a Claude Code SessionStart hook
    echo "$line" >> "$CLAUDE_ENV_FILE"
  fi
  export DOTNET_ROOT="$DOTNET_DIR" PATH="$DOTNET_DIR:$PATH"
fi

if [[ "${SKIP_BUILD:-}" == 1 ]]; then
  say "builds skipped (SKIP_BUILD=1)"
  exit 0
fi
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
say "build 1.70e"
dotnet build "$ROOT/src/Niagara2Efk/Niagara2Efk.csproj" -c Release -p:Platform=x64 -v quiet -nologo
say "build 1.8"
dotnet build "$ROOT/src/Niagara2Efk1803/Niagara2Efk1803.csproj" -c Release -v quiet -nologo
say "done: python3 tools/run_all_tests.py --work out/tests/all runs every suite"
