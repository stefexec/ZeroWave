# Application Store Publish Record

Keep this file in the application project root. Update it when package
metadata, listing copy, assets, privacy choices, or submission status changes.

Portal: <https://dev.cardputer.cc/#/upload>

## Package

- Debian package path: `build/zerowave_1.0.0_arm64.deb`
- Package: `zerowave`
- Version: `1.0.0`
- Architecture: `arm64`
- Maintainer: `Gurkenwerfer <stefan@gurk.dev>`
- APPLaunch desktop path: `/usr/share/APPLaunch/applications/zerowave.desktop`
- Executable path: `/usr/share/APPLaunch/bin/zerowave`

## Source and Ownership

- Public source repository: `https://github.com/stefexec/ZeroWave`
- Mirror repository: `https://git.gurk.dev/gurk/ZeroDMR`
- Expected GitHub uploader/owner: `stefexec`
- Hide uploader email: `false`

## Listing

- Application name: `ZeroWave`
- Share code: `ZWAV`
- One-line summary (maximum 80 characters): `2m/70cm All-Mode RTL-SDR DMR/YSF/FM/Pager/APRS/433/Sonde scanner`
- Description: `Native 2m/70cm all-mode RTL-SDR receiver and auto-scanner for M5Stack Cardputer Zero. Features wideband FFT spectrum sweeping with peak detection, Cortex-A53 optimized dsd-fme digital voice decoding (DMR 2-slot, YSF, D-STAR, M17, dPMR, NXDN, P25), offline RadioID callsign & BrandMeister talkgroup database downloader, analog NFM/AM/WFM scanner, POCSAG/FLEX pager, APRS, rtl_433 sensors, RS41 weather balloons, per-mode logs, and zero-drop PipeWire WAV recording & playback.`
- Categories (maximum 2): `Radio & Comms`, `Hardware & IoT`

## Assets

- 256 x 256 source icon: `zerowave.png`
- Icon packaged in `.deb`: `/usr/share/APPLaunch/share/images/zerowave.png`
- Screenshot 1 (320 x 170): `screenshots/dmr_live.png`
- Screenshot 2 (320 x 170): `screenshots/spectrum.png`
- Screenshot 3 (320 x 170): `screenshots/modes.png`
- Screenshot 4 (320 x 170): `screenshots/config.png`

## Preflight

- [x] `.deb` exists and can be parsed (`build/zerowave_1.0.0_arm64.deb`, 601 KB).
- [x] Control fields contain Package, Version, Architecture, and Maintainer.
- [x] Architecture is appropriate for the package (`arm64` with bundled Cortex-A53 `dsd-fme`, `libmbe.so.1.3`, `rs41mod`).
- [x] APPLaunch `.desktop`, executable, and PNG icon exist inside the package.
- [x] `install_path_policy.py` and `store_meta_policy.py` passed locally.
- [x] Package name ownership and update version are valid.
- [x] Listing copy and categories have been reviewed.
- [x] Four clean 320 x 170 baseline screenshots exist.
- [x] No credentials, tokens, or private server details are embedded.
- [x] The user explicitly approved external submission.

## Submission Result

- Status: `submitted (CI passed, device demo video posted — awaiting maintainer merge)`
- Submitted at: `2026-11-20`
- Demo video URL: `https://github.com/stefexec/ZeroWave/releases/download/v1.0.0/zerowave-1.0.0-device-demo.mp4`
- Buffer release URL: `https://github.com/stefexec/packages/releases/download/czdev-buffer/zerowave_1.0.0_arm64.deb`
- Tracking / Pull-Request URL: `https://github.com/CardputerZero/packages/pull/582`
