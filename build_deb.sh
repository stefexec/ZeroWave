#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

PKG="zerowave"
VER="1.0.0"
ARCH="arm64"
STAGE="build/staging-${PKG}"
DEB_OUT="build/${PKG}_${VER}_${ARCH}.deb"

rm -rf "$STAGE"
mkdir -p \
  "$STAGE/DEBIAN" \
  "$STAGE/usr/share/APPLaunch/applications" \
  "$STAGE/usr/share/APPLaunch/bin" \
  "$STAGE/usr/share/APPLaunch/share/images" \
  "$STAGE/usr/share/APPLaunch/apps/${PKG}/bin" \
  "$STAGE/usr/share/APPLaunch/apps/${PKG}/lib"

cat > "$STAGE/DEBIAN/control" <<'EOF'
Package: zerowave
Version: 1.0.0
Architecture: arm64
Maintainer: Gurkenwerfer <stefan@gurk.dev>
Section: hamradio
Priority: optional
Depends: python3, python3-pygame, python3-numpy, rtl-sdr, librtlsdr0, multimon-ng, rtl-433, sox, pulseaudio-utils, pipewire-bin, alsa-utils, libcodec2-1.2, libsndfile1, libpulse0, libncursesw6
Homepage: https://github.com/stefexec/ZeroWave
Description: 2m/70cm All-Mode RTL-SDR Scanner & Digital Voice Decoder for Cardputer Zero
 Native RTL-SDR wideband scanner and multi-mode decoder for M5Stack Cardputer Zero.
 Includes Cortex-A53 optimized dsd-fme (DMR, YSF, D-STAR, M17, dPMR, NXDN, P25),
 offline RadioID/BrandMeister callsign & talkgroup DB downloader, NFM/AM/WFM analog
 scanner, POCSAG/FLEX pager, APRS, rtl_433 sensors, RS41 radiosonde decoder,
 per-mode logs, and zero-drop PipeWire WAV recording & playback.
EOF

install -m 0644 zerowave.desktop "$STAGE/usr/share/APPLaunch/applications/zerowave.desktop"
install -m 0755 zerowave "$STAGE/usr/share/APPLaunch/bin/zerowave"
install -m 0644 zerowave.png "$STAGE/usr/share/APPLaunch/share/images/zerowave.png"

install -m 0755 zerowave.py "$STAGE/usr/share/APPLaunch/apps/${PKG}/zerowave.py"
install -m 0644 rtlsdr_sweep.py "$STAGE/usr/share/APPLaunch/apps/${PKG}/rtlsdr_sweep.py"
install -m 0644 dsd_runner.py "$STAGE/usr/share/APPLaunch/apps/${PKG}/dsd_runner.py"
install -m 0644 aux_decoders.py "$STAGE/usr/share/APPLaunch/apps/${PKG}/aux_decoders.py"
install -m 0644 radio_db.py "$STAGE/usr/share/APPLaunch/apps/${PKG}/radio_db.py"

# Bundle Cortex-A53 (-O3) native decoders inside app dir (Debian/Store policy compliant)
install -m 0755 vendor/arm64/dsd-fme "$STAGE/usr/share/APPLaunch/apps/${PKG}/bin/dsd-fme"
install -m 0755 vendor/arm64/rs41mod "$STAGE/usr/share/APPLaunch/apps/${PKG}/bin/rs41mod"
install -m 0644 vendor/arm64/libmbe.so.1.3 "$STAGE/usr/share/APPLaunch/apps/${PKG}/lib/libmbe.so.1.3"
ln -sf libmbe.so.1.3 "$STAGE/usr/share/APPLaunch/apps/${PKG}/lib/libmbe.so.1"
ln -sf libmbe.so.1 "$STAGE/usr/share/APPLaunch/apps/${PKG}/lib/libmbe.so"

dpkg-deb --root-owner-group -b "$STAGE" "$DEB_OUT"
rm -rf "$STAGE"
echo "Built $DEB_OUT"
ls -lh "$DEB_OUT"
