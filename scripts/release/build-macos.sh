#!/usr/bin/env bash
# Build a single Apple Silicon binary without Homebrew runtime dependencies.
set -Eeuo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
source "$repo/scripts/release/dependencies.env"
[[ "$(uname -s)" = Darwin && "$(uname -m)" = arm64 ]] || {
  echo 'This builder requires an Apple Silicon Mac.' >&2; exit 1;
}
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-15.0}"
build_root="${TTYD_RELEASE_BUILD:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/ttyd-release-macos}"
prefix="$build_root/prefix"
sources="$build_root/sources"
downloads="$build_root/downloads"
licenses="$build_root/licenses"
jobs="${TTYD_BUILD_JOBS:-$(sysctl -n hw.ncpu)}"
mkdir -p "$prefix" "$sources" "$downloads" "$licenses" "$repo/dist"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig"

fetch() {
  local url="$1" name="$2"
  if [ ! -s "$downloads/$name.tar.gz" ]; then
    curl -fL --retry 3 --connect-timeout 20 --max-time 300 "$url" -o "$downloads/$name.tar.gz.tmp"
    mv "$downloads/$name.tar.gz.tmp" "$downloads/$name.tar.gz"
  fi
  if [ ! -d "$sources/$name" ]; then
    mkdir -p "$sources/$name"
    tar -xzf "$downloads/$name.tar.gz" -C "$sources/$name" --strip-components=1
  fi
}

cmake_static() {
  local name="$1"; shift
  cmake -S "$sources/$name" -B "$build_root/$name" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DCMAKE_PREFIX_PATH="$prefix" -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DBUILD_SHARED_LIBS=OFF "$@"
  cmake --build "$build_root/$name" --parallel "$jobs"
  cmake --install "$build_root/$name"
}

fetch "https://zlib.net/fossils/zlib-$ZLIB_VERSION.tar.gz" zlib
cmake_static zlib -DZLIB_BUILD_SHARED=OFF -DZLIB_BUILD_STATIC=ON -DZLIB_BUILD_TESTING=OFF

fetch "https://s3.amazonaws.com/json-c_releases/releases/json-c-$JSON_C_VERSION.tar.gz" json-c
cmake_static json-c -DBUILD_TESTING=OFF -DDISABLE_WERROR=ON

fetch "https://dist.libuv.org/dist/v$LIBUV_VERSION/libuv-v$LIBUV_VERSION.tar.gz" libuv
cmake_static libuv -DLIBUV_BUILD_SHARED=OFF -DLIBUV_BUILD_TESTS=OFF -DLIBUV_BUILD_BENCH=OFF

fetch "https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VERSION/openssl-$OPENSSL_VERSION.tar.gz" openssl
if [ ! -s "$prefix/lib/libssl.a" ]; then
  (
    cd "$sources/openssl"
    ./Configure darwin64-arm64-cc no-shared no-module no-tests \
      --prefix="$prefix" --libdir=lib
    make -j"$jobs"
    make install_sw
  )
fi

fetch "https://github.com/warmcat/libwebsockets/archive/refs/tags/v$LIBWEBSOCKETS_VERSION.tar.gz" libwebsockets
# The upstream package lists the shared target even in static-only builds.
sed -i '' 's/ websockets_shared//g' "$sources/libwebsockets/cmake/libwebsockets-config.cmake.in"
cmake_static libwebsockets \
  -DCMAKE_FIND_LIBRARY_SUFFIXES=.a -DOPENSSL_USE_STATIC_LIBS=TRUE -DOPENSSL_ROOT_DIR="$prefix" \
  -DZLIB_LIBRARY="$prefix/lib/libz.a" \
  -DLWS_LIBUV_LIBRARIES="$prefix/lib/libuv.a" -DLWS_LIBUV_INCLUDE_DIRS="$prefix/include" \
  -DLWS_OPENSSL_LIBRARIES="$prefix/lib/libssl.a;$prefix/lib/libcrypto.a" \
  -DLWS_OPENSSL_INCLUDE_DIRS="$prefix/include" \
  -DLWS_WITH_STATIC=ON -DLWS_WITH_SHARED=OFF -DLWS_WITHOUT_TESTAPPS=ON \
  -DLWS_WITH_SSL=ON -DLWS_WITH_LIBUV=ON -DLWS_UNIX_SOCK=ON \
  -DLWS_WITH_HTTP2=ON -DLWS_WITH_HTTP_STREAM_COMPRESSION=ON \
  -DLWS_WITHOUT_CLIENT=ON -DLWS_WITHOUT_EXTENSIONS=OFF \
  -DLWS_WITH_LIBEVENT=OFF -DLWS_WITH_LIBEV=OFF -DLWS_WITH_GLIB=OFF \
  -DLWS_WITH_UPNG=OFF -DLWS_WITH_JPEG=OFF -DLWS_WITH_DLO=OFF \
  -DLWS_WITH_SYS_STATE=OFF -DLWS_WITH_SYS_SMD=OFF -DLWS_WITH_SECURE_STREAMS=OFF \
  -DLWS_CTEST_INTERNET_AVAILABLE=OFF

cmake -S "$repo" -B "$build_root/ttyd" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$prefix" \
  -DCMAKE_FIND_LIBRARY_SUFFIXES=.a -DOPENSSL_USE_STATIC_LIBS=TRUE \
  -DZLIB_LIBRARY="$prefix/lib/libz.a" \
  -DOPENSSL_SSL_LIBRARY="$prefix/lib/libssl.a" \
  -DOPENSSL_CRYPTO_LIBRARY="$prefix/lib/libcrypto.a" -DOPENSSL_INCLUDE_DIR="$prefix/include" \
  -DOPENSSL_ROOT_DIR="$prefix" -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET"
cmake --build "$build_root/ttyd" --parallel "$jobs"
install -m 755 "$build_root/ttyd/ttyd" "$repo/dist/ttyd.macos-arm64"
strip -x "$repo/dist/ttyd.macos-arm64"
codesign --force --sign - "$repo/dist/ttyd.macos-arm64"

# macOS system libraries remain dynamic; third-party libraries must be static.
otool -L "$repo/dist/ttyd.macos-arm64"
otool -L "$repo/dist/ttyd.macos-arm64" | tail -n +2 | awk '{print $1}' | while IFS= read -r lib; do
  case "$lib" in /usr/lib/*|/System/Library/*) ;; *)
    echo "Unexpected runtime dependency: $lib" >&2; exit 1 ;;
  esac
done
codesign --verify --strict "$repo/dist/ttyd.macos-arm64"
"$repo/dist/ttyd.macos-arm64" --version

cp "$repo/LICENSE" "$licenses/ttyd-LICENSE"
for name in zlib json-c libuv openssl libwebsockets; do
  mkdir -p "$licenses/$name"
  for notice in LICENSE* COPYING* NOTICE*; do
    if [ -f "$sources/$name/$notice" ]; then
      cp "$sources/$name/$notice" "$licenses/$name/$notice"
    fi
  done
done
cp "$repo/scripts/release/dependencies.env" "$licenses/dependencies.env"
COPYFILE_DISABLE=1 tar -czf "$repo/dist/THIRD_PARTY_LICENSES.tar.gz" -C "$licenses" .
