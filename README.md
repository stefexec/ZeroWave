# ZeroWave

Native **2m/70cm All-Mode RTL-SDR Scanner, Digital Voice & Telemetry Suite** for the **M5Stack Cardputer Zero** (Raspberry Pi CM0, 320×170 RGB565 Framebuffer + TCA8418 Keyboard + ES8389 Audio).

## 6 Receiver Modes (Quick-Switch Grid on `Q`)
1. **`VOICE` (Digital Voice):** DMR (2-Slot), YSF (C4FM), D-STAR, M17 (Codec2), dPMR, NXDN48/96, P25 Ph1/Ph2, DMR/P25 Trunking, and Auto-Detect (`-fa`) powered by `dsd-fme` (`-O3 -mcpu=cortex-a53`). Includes hybrid `AUTO` wideband FFT peak-finder, `LIST` cluster-RSSI fast scanner, and `TUNER` manual mode.
2. **`ANALOG` (FM / AM):** NFM (2m/70cm Relais, PMR446, Marine, Freenet), AM (Aviation 118–137 MHz), and WFM (FM Broadcast) with adjustable squelch and band scanning.
3. **`PAGER` (POCSAG / FLEX):** Live POCSAG 512/1200/2400 & FLEX pager feed via `multimon-ng` (DAPNET `439.9875 MHz` preset included).
4. **`APRS`:** Live AFSK1200 APRS packet feed (`144.800 MHz` EU & `145.825 MHz` ISS presets).
5. **`ISM 433`:** Live 433/868 MHz sensor, weather station & TPMS cards via `rtl_433`.
6. **`SONDE`:** 400–406 MHz weather balloon spectrum sweep & Vaisala RS41 live telemetry via `rs41mod`.

## Highlights
- **Offline Callsign & Talkgroup DB Downloader (`5:CFG`):** Downloads RadioID.net (`~270k` DMR IDs: Callsign, Name, City, Country) + BrandMeister Talkgroups into a fast local SQLite DB.
- **WAV Recording & In-App Playback (`R` & `4:LOG`):** Per-call digital WAV recording (`dsd-fme -P`) and analog WAV recording (`sox`) to `~/ZeroWave_Recordings/`, playable directly on the Cardputer Zero speaker via `4:LOG → RECORDINGS`.
- **Cardputer Zero Native Controls:** Full TCA8418 Normal / Shift / Sym / Fn keymap (`Q` Quick-Mode Grid, `SYM+.` decimal entry, `FN+A/S/D` Mute/Vol, ES8389 `-6 dB` speaker stability fix, 3-second `ESC` hold exit overlay).

## Install
```sh
./install.sh
```
Installs `ZeroWave` to `/usr/share/APPLaunch/`.
