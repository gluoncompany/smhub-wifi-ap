#!/bin/sh
# Cross-compiles a static hostapd (+ hostapd_cli) for the SMHUB (riscv64, glibc) on Debian/Ubuntu x86_64.
# Output: ../app/hostapd and ../app/hostapd_cli (picked up by build.py).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
B="$HERE/build"; P="$B/sysroot"
HOSTAPD_VER=2.11; LIBNL_VER=3.11.0; OPENSSL_VER=3.0.15
mkdir -p "$B" "$P"; cd "$B"
sudo apt-get install -y gcc-riscv64-linux-gnu libc6-dev-riscv64-cross make flex bison pkg-config curl xz-utils
[ -f openssl-$OPENSSL_VER.tar.gz ] || curl -fLO https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VER/openssl-$OPENSSL_VER.tar.gz
[ -f libnl-$LIBNL_VER.tar.gz ] || curl -fLO https://github.com/thom311/libnl/releases/download/libnl$(echo $LIBNL_VER | tr . _)/libnl-$LIBNL_VER.tar.gz
[ -f hostapd-$HOSTAPD_VER.tar.gz ] || curl -fLO https://w1.fi/releases/hostapd-$HOSTAPD_VER.tar.gz
for f in openssl-$OPENSSL_VER libnl-$LIBNL_VER hostapd-$HOSTAPD_VER; do [ -d $f ] || tar xzf $f.tar.gz; done

(cd openssl-$OPENSSL_VER && ./Configure linux64-riscv64 no-shared no-tests no-engine no-dso \
    --cross-compile-prefix=riscv64-linux-gnu- --prefix="$P" --libdir=lib && make -j"$(nproc)" build_libs && make install_dev)
(cd libnl-$LIBNL_VER && ./configure --host=riscv64-linux-gnu --prefix="$P" --disable-shared --enable-static --disable-cli \
    && make -j"$(nproc)" && make install)
cd hostapd-$HOSTAPD_VER/hostapd
cp "$HERE/hostapd.config" .config
make clean >/dev/null 2>&1 || true
make -j"$(nproc)" CC=riscv64-linux-gnu-gcc EXTRA_CFLAGS="-I$P/include -I$P/include/libnl3 -Os" \
    LDFLAGS="-static -L$P/lib" PKG_CONFIG=false hostapd hostapd_cli
riscv64-linux-gnu-strip hostapd hostapd_cli
cp hostapd hostapd_cli "$HERE/../app/"
echo "OK: app/hostapd and app/hostapd_cli"
