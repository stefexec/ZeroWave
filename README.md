# ZeroDMR

Native **RTL-SDR DMR Auto-Scanner, Channel-List Scanner & Digital Voice Decoder** for the **M5Stack Cardputer Zero** (Raspberry Pi CM0, 320×170 RGB565 Framebuffer + TCA8418 Keyboard + ES8389 Audio).

## Features
- **Hybrid Scanning Engine:**
  - **AUTO Band-Sweep:** Wideband `librtlsdr` + NumPy FFT peak finder (68 ms max-hold to catch 30 ms DMR TDMA bursts, peak-relative `-12 dB` bandwidth filtering so strong local signals are detected accurately) with automatic `dsd-fme` verification.
  - **LIST Fast Scanner:** Cluster-based RSSI pre-check across saved/discovered channels (scans dozens of channels in < 0.3 s) before handing active carriers to `dsd-fme`.
  - **TUNER Direct Mode:** Manual frequency entry (`F`) or step tuning (`←/→` ±12.5 kHz, `↑/↓` ±100 kHz).
- **Live DMR Dashboard (320×170):**
  - Real-time spectrum display with threshold line & peak markers during sweeps.
  - Dual-slot DMR cards (`SLOT 1` & `SLOT 2`) showing Talkgroup (`TG`), Radio ID (`SRC`), Color Code (`CC`), BS/MS mode, Talker Alias, Clear/Encrypted indicator, and live `VOICE` badge.
  - Auto-save discovered DMR frequencies + persistent Call Log (`LOG`) + temporary Skip & permanent Blacklist.
- **Native Cardputer Zero Controls:**
  - Full TCA8418 Normal / Shift / Sym / Fn keymap (`SYM+.` for decimal point, `FN+S`/`FN+D` & `SYM +/-` for volume, `V` for mute, 3-second `ESC` hold overlay to exit cleanly).

## Requirements on Cardputer Zero
- `rtl-sdr`, `librtlsdr-dev` (with DVB-T kernel driver `dvb_usb_rtl28xxu` blacklisted)
- `mbelib` (`ambe_tones` branch) & `dsd-fme` compiled with `-O3 -mcpu=cortex-a53`
- Python 3 with `numpy` and `pygame` (preinstalled on Cardputer Zero)

## Install
```sh
./install.sh
```
Installs `ZeroDMR` directly into `/usr/share/APPLaunch/`.
