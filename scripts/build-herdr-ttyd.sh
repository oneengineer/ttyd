#!/usr/bin/env bash
# Build this checkout and install a separate binary in the current user's bin.
set -Eeuo pipefail
umask 077

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
build_dir="${HERDR_WEB_BUILD:-$repo/build/herdr-auth}"
bin_dir="${HERDR_WEB_BIN:-$HOME/.local/bin}"
case "$(uname -s)" in
  Linux|Darwin) ;;
  *) echo 'On Windows, build with CMake and select the resulting exe with HERDR_WEB_TTYD.' >&2; exit 1 ;;
esac
command -v cmake >/dev/null || { echo 'Missing build dependency: cmake' >&2; exit 1; }

options=(-S "$repo" -B "$build_dir" -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5)
if command -v brew >/dev/null; then options+=("-DCMAKE_PREFIX_PATH=$(brew --prefix)"); fi
cmake "${options[@]}"
cmake --build "$build_dir" --parallel 4
"$build_dir/ttyd" --version | grep -Fq herdr-auth1

mkdir -p "$bin_dir"
temporary="$bin_dir/ttyd-herdr-auth.tmp.$$"
trap 'rm -f -- "$temporary"' EXIT
cp "$build_dir/ttyd" "$temporary"
chmod 755 "$temporary"
mv "$temporary" "$bin_dir/ttyd-herdr-auth"
printf 'Installed: %s/ttyd-herdr-auth\n' "$bin_dir"
