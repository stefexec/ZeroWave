#!/usr/bin/env python3
"""
ZeroDMR — Hybrid RTL-SDR DMR Auto-Scanner, Channel-List Scanner & Decoder
for M5Stack Cardputer Zero (320x170 RGB565 Framebuffer + Raw Evdev Keyboard).
"""

from __future__ import annotations

import collections
import datetime
import json
import math
import os
import select
import signal
import struct
import subprocess
import sys
import threading
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
sys.modules["pkg_resources"] = type(
    "P", (), {"resource_stream": lambda *_: None, "resource_exists": lambda *_: False}
)()

import numpy as np
import pygame

from dsd_runner import DsdRunner, DsdState
from rtlsdr_sweep import PeakCandidate, RtlSdrError, RtlSdrSweeper, plan_band_centers

# ==============================================================================
# DISPLAY & THEME CONSTANTS
# ==============================================================================
W, H = 320, 170

BG = (7, 10, 14)
SURFACE = (16, 21, 27)
ELEVATED = (24, 31, 40)
BORDER = (38, 48, 60)
MINT = (30, 215, 166)
MINT_DIM = (18, 130, 102)
MINT_DARK = (10, 46, 38)
CYAN = (72, 196, 255)
CYAN_DARK = (14, 48, 68)
TEXT = (245, 248, 252)
SUBTEXT = (142, 154, 168)
MUTED = (82, 92, 104)
DANGER = (255, 86, 86)
DANGER_DARK = (62, 18, 22)
WARN = (255, 182, 42)
WARN_DARK = (62, 42, 10)
GREEN = (56, 224, 118)
GREEN_DARK = (14, 52, 28)

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
KBD_DEV = "/dev/input/by-path/platform-3f804000.i2c-event"

CONFIG_DIR = os.path.expanduser("~/.config/zerodmr")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
CHANNELS_FILE = os.path.join(CONFIG_DIR, "channels.json")
BLACKLIST_FILE = os.path.join(CONFIG_DIR, "blacklist.json")
LOG_FILE = os.path.join(CONFIG_DIR, "call_log.json")

# ==============================================================================
# EVDEV KEYCODES & CARDPUTER ZERO FULL KEYMAP (Normal + Shift + Sym + Fn layers)
# ==============================================================================
ESC_HOLD_SECONDS = 3.0
ESC_OVERLAY_DELAY = 0.45

KEY_ESC = 1
KEY_1, KEY_2, KEY_3, KEY_4, KEY_5 = 2, 3, 4, 5, 6
KEY_6, KEY_7, KEY_8, KEY_9, KEY_0 = 7, 8, 9, 10, 11
KEY_MINUS = 12
KEY_EQUAL = 13
KEY_BACKSPACE = 14
KEY_TAB = 15
KEY_Q, KEY_W, KEY_E, KEY_R, KEY_T = 16, 17, 18, 19, 20
KEY_Y, KEY_U, KEY_I, KEY_O, KEY_P = 21, 22, 23, 24, 25
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_A, KEY_S, KEY_D, KEY_F, KEY_G = 30, 31, 32, 33, 34
KEY_H, KEY_J, KEY_K, KEY_L = 35, 36, 37, 38
KEY_LEFTSHIFT = 42
KEY_Z, KEY_X, KEY_C, KEY_V, KEY_B = 44, 45, 46, 47, 48
KEY_N, KEY_M = 49, 50
KEY_COMMA = 51
KEY_DOT = 52
KEY_SLASH = 53
KEY_RIGHTSHIFT = 54
KEY_SPACE = 57
KEY_RIGHTCTRL = 97
KEY_UP = 103
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_DOWN = 108
KEY_DELETE = 111
KEY_MUTE = 113          # Cardputer FN+A
KEY_VOLUMEDOWN = 114    # Cardputer FN+S
KEY_VOLUMEUP = 115      # Cardputer FN+D

# Full Cardputer Zero TCA8418 evdev map (matches linux,keymap + keymap1 Sym + keymap2 Fn)
EVDEV_CHAR_MAP = {
    # Normal layer
    2: ("1", "1"), 3: ("2", "2"), 4: ("3", "3"), 5: ("4", "4"), 6: ("5", "5"),
    7: ("6", "6"), 8: ("7", "7"), 9: ("8", "8"), 10: ("9", "9"), 11: ("0", "0"),
    12: ("-", "_"), 13: ("=", "+"),
    16: ("q", "Q"), 17: ("w", "W"), 18: ("e", "E"), 19: ("r", "R"), 20: ("t", "T"),
    21: ("y", "Y"), 22: ("u", "U"), 23: ("i", "I"), 24: ("o", "O"), 25: ("p", "P"),
    30: ("a", "A"), 31: ("s", "S"), 32: ("d", "D"), 33: ("f", "F"), 34: ("g", "G"),
    35: ("h", "H"), 36: ("j", "J"), 37: ("k", "K"), 38: ("l", "L"),
    44: ("z", "Z"), 45: ("x", "X"), 46: ("c", "C"), 47: ("v", "V"), 48: ("b", "B"),
    49: ("n", "N"), 50: ("m", "M"), 57: (" ", " "),
    # Sym layer (linux,keymap1 -> tca8418_keypad_m5stack_keymap.map)
    26: ("!", "!"), 27: ("@", "@"), 39: ("#", "#"), 40: ("$", "$"),
    41: ("%", "%"), 43: ("^", "^"), 51: ("&", "&"), 52: ("*", "*"),
    53: ("(", "("), 94: (")", ")"), 55: ("~", "~"), 69: ("`", "`"),
    70: ("_", "_"), 71: ("-", "-"), 72: ("+", "+"), 73: ("=", "="),
    74: ("[", "["), 75: ("]", "]"), 76: ("{", "{"), 77: ("}", "}"),
    79: (";", ";"), 80: (":", ":"), 81: ("'", "'"), 82: ('"', '"'),
    83: ("<", "<"), 85: (">", ">"), 86: ("\\", "\\"), 89: ("|", "|"),
    90: (",", ","), 91: (".", "."), 92: ("/", "/"), 93: ("?", "?"),
}

# ==============================================================================
# DEFAULT BANDS & CHANNELS (Germany / EU focused + customizable)
# ==============================================================================
DEFAULT_BANDS = [
    {"id": "ham70_rep", "name": "70cm Ham DMR Relais", "start_hz": 438_200_000, "end_hz": 439_425_000, "step_hz": 12_500, "enabled": True},
    {"id": "ham70_sim", "name": "70cm Ham Simplex", "start_hz": 433_400_000, "end_hz": 433_600_000, "step_hz": 12_500, "enabled": True},
    {"id": "pmr446_d", "name": "PMR446 Digital", "start_hz": 446_100_000, "end_hz": 446_200_000, "step_hz": 6_250, "enabled": True},
    {"id": "ham70_lo", "name": "70cm Ham 430-431", "start_hz": 430_000_000, "end_hz": 431_000_000, "step_hz": 12_500, "enabled": False},
    {"id": "ham2m", "name": "2m Ham Digital", "start_hz": 145_575_000, "end_hz": 145_800_000, "step_hz": 12_500, "enabled": False},
    {"id": "uhf_440", "name": "UHF 440-450 MHz", "start_hz": 440_000_000, "end_hz": 450_000_000, "step_hz": 12_500, "enabled": False},
    {"id": "uhf_450", "name": "UHF 450-460 MHz", "start_hz": 450_000_000, "end_hz": 460_000_000, "step_hz": 12_500, "enabled": False},
    {"id": "uhf_460", "name": "UHF 460-470 MHz", "start_hz": 460_000_000, "end_hz": 470_000_000, "step_hz": 12_500, "enabled": False},
    {"id": "custom", "name": "Custom Band", "start_hz": 438_000_000, "end_hz": 440_000_000, "step_hz": 12_500, "enabled": False},
]

DEFAULT_CHANNELS = [
    {"freq_hz": 433_450_000, "name": "70cm DMR Simp 1", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 433_475_000, "name": "70cm DMR Simp 2", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 433_500_000, "name": "70cm Call", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 438_225_000, "name": "70cm DMR Rep", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 439_512_500, "name": "70cm DMR Rep", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 446_106_250, "name": "PMR446 DMR Ch1", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 446_118_750, "name": "PMR446 DMR Ch2", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 446_131_250, "name": "PMR446 DMR Ch3", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 446_143_750, "name": "PMR446 DMR Ch4", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
]

DECODER_MODES = [
    ("-fs", "DMR Only (-fs)"),
    ("-fa", "Auto Digital (-fa)"),
    ("-fm", "dPMR (-fm)"),
    ("-fi", "NXDN48 (-fi)"),
    ("-fn", "NXDN96 (-fn)"),
    ("-f1", "P25 Ph1 (-f1)"),
    ("-fy", "YSF (-fy)"),
    ("-fd", "D-STAR (-fd)"),
]

GAIN_OPTIONS = [0, 16, 24, 32, 36, 42, 48]  # 0 = Auto AGC
BW_OPTIONS = [8, 12, 16, 24]                # RTL VFO bandwidth in kHz


def fmt_mhz(freq_hz: int) -> str:
    return f"{freq_hz / 1_000_000.0:.4f}"


def fmt_mhz_short(freq_hz: int) -> str:
    return f"{freq_hz / 1_000_000.0:.3f}"


# ==============================================================================
# AUDIO VOLUME HELPER (PipeWire / PulseAudio)
# ==============================================================================
class AudioControl:
    def __init__(self) -> None:
        self.volume_pct = 80
        self.muted = False
        self._sync_from_system()

    def _run_pactl(self, args: list[str]) -> str:
        try:
            env = os.environ.copy()
            env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
            out = subprocess.check_output(["pactl"] + args, env=env, stderr=subprocess.DEVNULL, timeout=1.0)
            return out.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _sync_from_system(self) -> None:
        out = self._run_pactl(["get-sink-volume", "@DEFAULT_SINK@"])
        for tok in out.split():
            if tok.endswith("%") and tok[:-1].isdigit():
                self.volume_pct = max(0, min(150, int(tok[:-1])))
                break
        m = self._run_pactl(["get-sink-mute", "@DEFAULT_SINK@"])
        if "yes" in m.lower():
            self.muted = True

    def set_volume(self, pct: int) -> None:
        self.volume_pct = max(0, min(100, int(pct)))
        if self.muted and self.volume_pct > 0:
            self.muted = False
            threading.Thread(
                target=self._run_pactl,
                args=(["set-sink-mute", "@DEFAULT_SINK@", "0"],),
                daemon=True,
            ).start()
        threading.Thread(
            target=self._run_pactl,
            args=(["set-sink-volume", "@DEFAULT_SINK@", f"{self.volume_pct}%"],),
            daemon=True,
        ).start()

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        threading.Thread(
            target=self._run_pactl,
            args=(["set-sink-mute", "@DEFAULT_SINK@", "1" if self.muted else "0"],),
            daemon=True,
        ).start()
        return self.muted


# ==============================================================================
# HYBRID SCANNER ENGINE (AUTO BAND SWEEP + LIST RSSI PRECHECK + DSD-FME)
# ==============================================================================
class ZeroDmrEngine:
    """
    Coordinates exclusive access to the RTL-SDR between:
      - RtlSdrSweeper (fast 1.8 MHz FFT snapshots & peak/channel detection)
      - DsdRunner (dsd-fme digital voice verification & decoding)
    """

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sweeper = RtlSdrSweeper(device_index=0)
        self.dsd = DsdRunner()
        self.dsd.on_call_event = self._on_call_event

        # Persisted settings
        self.config = {
            "mode": "AUTO",           # AUTO, LIST, TUNER
            "decoder_flag": "-fs",    # -fs = DMR
            "gain_db": 0,             # 0 = RTL Hardware AGC (recommended by dsd-fme)
            "bw_khz": 12,             # 8, 12, 16, 24 kHz VFO bandwidth
            "ppm": 0,
            "threshold_db": 11.0,
            "verify_sec": 2.2,
            "hang_sec": 4.0,
            "cooldown_sec": 22.0,
            "auto_save_found": True,
            "manual_freq_hz": 438_425_000,
            "bands": [dict(b) for b in DEFAULT_BANDS],
        }
        self.channels: list[dict] = [dict(c) for c in DEFAULT_CHANNELS]
        self.blacklist: set[int] = set()
        self.call_log: collections.deque[dict] = collections.deque(maxlen=200)

        self._load_all()

        # Live runtime state
        self.phase = "IDLE"           # IDLE, SWEEP, CHECK, LOCKED, TUNER, ERROR
        self.hold = False
        self.paused = False
        self.active_freq_hz: int = int(self.config["manual_freq_hz"])
        self.active_label: str = ""
        self.active_snr_db: float = 0.0
        self.status_text: str = "Starting RTL-SDR..."
        self.error_text: str = ""
        self.sweep_band_name: str = ""
        self.sweep_center_hz: int = 0
        self.sweep_freqs: np.ndarray | None = None
        self.sweep_psd: np.ndarray | None = None
        self.sweep_noise: float = -45.0
        self.sweep_peaks: list[PeakCandidate] = []
        self.cooldowns: dict[int, float] = {}  # freq_hz -> expire_ts
        self.skipped_count: int = 0
        self.found_count: int = 0
        self.muted: bool = False

        # Control flags for worker thread
        self._stop_evt = threading.Event()
        self._interrupt_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self.on_toast: callable | None = None

    # --- Persistence ----------------------------------------------------------
    def _load_all(self) -> None:
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    for k, v in saved.items():
                        if k == "bands" and isinstance(v, list) and v:
                            self.config["bands"] = v
                        elif k in self.config:
                            self.config[k] = v
        except Exception:
            pass
        try:
            if os.path.exists(CHANNELS_FILE):
                with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
                    ch = json.load(f)
                if isinstance(ch, list) and ch:
                    self.channels = ch
        except Exception:
            pass
        try:
            if os.path.exists(BLACKLIST_FILE):
                with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
                    bl = json.load(f)
                if isinstance(bl, list):
                    self.blacklist = set(int(x) for x in bl)
        except Exception:
            pass
        try:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    logs = json.load(f)
                if isinstance(logs, list):
                    for item in logs[-200:]:
                        if isinstance(item, dict):
                            self.call_log.append(item)
        except Exception:
            pass

    def save_config(self) -> None:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception:
            pass

    def save_channels(self) -> None:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.channels, f, indent=2)
        except Exception:
            pass

    def save_blacklist(self) -> None:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
                json.dump(sorted(self.blacklist), f, indent=2)
        except Exception:
            pass

    def save_log(self) -> None:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self.call_log), f)
        except Exception:
            pass

    # --- Public controls ------------------------------------------------------
    def start(self) -> None:
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        self._interrupt_evt.set()
        self.dsd.stop()
        self.sweeper.close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.save_config()
        self.save_channels()
        self.save_blacklist()
        self.save_log()

    def set_mode(self, mode: str) -> None:
        with self.lock:
            self.config["mode"] = mode
            self.hold = False
            self.paused = False
        self.save_config()
        self._interrupt_evt.set()

    def tune_manual(self, freq_hz: int, label: str = "") -> None:
        with self.lock:
            self.config["mode"] = "TUNER"
            self.config["manual_freq_hz"] = int(freq_hz)
            self.active_freq_hz = int(freq_hz)
            self.active_label = label or self._lookup_channel_name(int(freq_hz))
            self.hold = False
            self.paused = False
        self.save_config()
        self._interrupt_evt.set()

    def step_manual(self, delta_hz: int) -> None:
        with self.lock:
            new_f = max(24_000_000, min(1_700_000_000, int(self.active_freq_hz) + int(delta_hz)))
            self.config["mode"] = "TUNER"
            self.config["manual_freq_hz"] = new_f
            self.active_freq_hz = new_f
            self.active_label = self._lookup_channel_name(new_f)
        self._interrupt_evt.set()

    def toggle_hold(self) -> bool:
        with self.lock:
            if self.config["mode"] == "TUNER":
                self.paused = not self.paused
                self._interrupt_evt.set()
                return self.paused
            self.hold = not self.hold
            return self.hold

    def skip_current(self, permanent: bool = False) -> int:
        with self.lock:
            f = int(self.active_freq_hz)
            if f > 0:
                if permanent:
                    self.blacklist.add(f)
                    self.save_blacklist()
                else:
                    self.cooldowns[f] = time.time() + float(self.config["cooldown_sec"])
                self.hold = False
        self._interrupt_evt.set()
        return f

    def save_favorite(self, freq_hz: int | None = None, cc: str = "", tg: str = "", source: str = "FAV") -> bool:
        with self.lock:
            f = int(freq_hz if freq_hz is not None else self.active_freq_hz)
            if f <= 0:
                return False
            for ch in self.channels:
                if abs(int(ch["freq_hz"]) - f) <= 4_000:
                    if cc:
                        ch["cc"] = cc
                    if tg:
                        ch["last_tg"] = tg
                    ch["hits"] = int(ch.get("hits", 0)) + 1
                    if source == "FAV":
                        ch["source"] = "FAV"
                    self.save_channels()
                    return False
            self.channels.insert(0, {
                "freq_hz": f,
                "name": f"DMR {fmt_mhz(f)}" if source == "AUTO" else f"Fav {fmt_mhz(f)}",
                "cc": cc,
                "last_tg": tg,
                "hits": 1,
                "source": source,
            })
            self.save_channels()
            return True

    def notify_config_changed(self) -> None:
        self.save_config()
        self._interrupt_evt.set()

    def _lookup_channel_name(self, freq_hz: int) -> str:
        for ch in self.channels:
            if abs(int(ch["freq_hz"]) - freq_hz) <= 4_000:
                return str(ch.get("name", ""))
        return ""

    def _is_ignored(self, freq_hz: int) -> bool:
        now = time.time()
        for bf in self.blacklist:
            if abs(bf - freq_hz) <= 6_250:
                return True
        # Clean expired cooldowns
        expired = [k for k, exp in self.cooldowns.items() if exp <= now]
        for k in expired:
            del self.cooldowns[k]
        for cf in self.cooldowns:
            if abs(cf - freq_hz) <= 6_250:
                return True
        return False

    def _on_call_event(self, ev: dict) -> None:
        with self.lock:
            self.call_log.appendleft(ev)
            self.found_count += 1
            if self.config.get("auto_save_found", True):
                self.save_favorite(
                    freq_hz=ev["freq_hz"],
                    cc=ev.get("cc", ""),
                    tg=ev.get("tg", ""),
                    source="AUTO",
                )
        self.save_log()

    # --- Main background engine loop ------------------------------------------
    def _worker_loop(self) -> None:
        while not self._stop_evt.is_set():
            self._interrupt_evt.clear()
            with self.lock:
                mode = self.config["mode"]
                paused = self.paused

            if paused:
                self.dsd.stop()
                self.sweeper.close()
                with self.lock:
                    self.phase = "IDLE"
                    self.status_text = "Paused (SPACE to resume)"
                self._interrupt_evt.wait(0.25)
                continue

            try:
                if mode == "TUNER":
                    self._run_tuner_cycle()
                elif mode == "LIST":
                    self._run_list_cycle()
                else:
                    self._run_auto_cycle()
            except RtlSdrError as e:
                self.dsd.stop()
                self.sweeper.close()
                with self.lock:
                    self.phase = "ERROR"
                    self.error_text = str(e)[:46]
                    self.status_text = f"SDR Error: {self.error_text}"
                self._interrupt_evt.wait(2.0)
            except Exception as e:
                self.dsd.stop()
                self.sweeper.close()
                with self.lock:
                    self.phase = "ERROR"
                    self.error_text = str(e)[:46]
                    self.status_text = f"Err: {self.error_text}"
                self._interrupt_evt.wait(1.5)

    def _run_tuner_cycle(self) -> None:
        self.sweeper.close()
        with self.lock:
            freq = int(self.config["manual_freq_hz"])
            self.active_freq_hz = freq
            self.active_label = self._lookup_channel_name(freq) or "Manual Tuner"
            self.phase = "TUNER"
            self.status_text = f"Monitoring {fmt_mhz(freq)} MHz..."
            self.error_text = ""
            cfg = dict(self.config)

        self.dsd.start(
            freq_hz=freq,
            mode_flag=str(cfg["decoder_flag"]),
            gain_db=int(cfg["gain_db"]),
            ppm=int(cfg["ppm"]),
            bw_khz=int(cfg.get("bw_khz", 12)),
            squelch=0,
            vol=2,
            muted=False,
        )

        while not self._stop_evt.is_set() and not self._interrupt_evt.is_set():
            st = self.dsd.get_state()
            with self.lock:
                if st.error:
                    self.error_text = st.error
                    self.phase = "ERROR"
                    self.status_text = st.error[:46]
                elif st.synced:
                    self.phase = "LOCKED"
                    self.status_text = f"{st.protocol or 'DMR'} LOCK CC:{st.color_code or '-'}"
                else:
                    self.phase = "TUNER"
                    self.status_text = f"Listening {fmt_mhz(freq)} MHz (no sync)"
            if not st.running:
                time.sleep(0.5)
                break
            self._interrupt_evt.wait(0.15)

        self.dsd.stop()

    def _run_list_cycle(self) -> None:
        with self.lock:
            ch_list = [int(c["freq_hz"]) for c in self.channels if not self._is_ignored(int(c["freq_hz"]))]
            cfg = dict(self.config)

        if not ch_list:
            with self.lock:
                self.phase = "IDLE"
                self.status_text = "Channel list empty! Add in LIST tab."
            self._interrupt_evt.wait(0.6)
            return

        self.dsd.stop()
        gain = None if int(cfg["gain_db"]) == 0 else float(cfg["gain_db"])
        self.sweeper.open(gain_db=gain, ppm=int(cfg["ppm"]))

        with self.lock:
            self.phase = "SWEEP"
            self.sweep_band_name = f"LIST ({len(ch_list)} ch)"
            self.status_text = f"Checking {len(ch_list)} saved channels..."
            self.error_text = ""

        def _on_win(center: int, freqs: np.ndarray, psd: np.ndarray, nf: float) -> None:
            with self.lock:
                self.sweep_center_hz = center
                self.sweep_freqs = freqs
                self.sweep_psd = psd
                self.sweep_noise = nf
                self.active_freq_hz = center

        hits = self.sweeper.measure_channels_fast(
            ch_list,
            threshold_db=float(cfg["threshold_db"]),
            on_window=_on_win,
        )

        with self.lock:
            self.sweep_peaks = hits

        if self._interrupt_evt.is_set() or self._stop_evt.is_set():
            return

        if not hits:
            self._interrupt_evt.wait(0.12)
            return

        # Verify strongest hit(s) with dsd-fme
        self.sweeper.close()
        for peak in hits[:3]:
            if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                break
            label = self._lookup_channel_name(peak.freq_hz) or "Saved List"
            locked = self._verify_and_monitor(peak, label)
            if locked:
                break

    def _run_auto_cycle(self) -> None:
        with self.lock:
            enabled_bands = [dict(b) for b in self.config["bands"] if b.get("enabled")]
            cfg = dict(self.config)

        if not enabled_bands:
            with self.lock:
                self.phase = "IDLE"
                self.status_text = "No band enabled! Enable in BANDS tab."
            self._interrupt_evt.wait(0.6)
            return

        self.dsd.stop()
        gain = None if int(cfg["gain_db"]) == 0 else float(cfg["gain_db"])
        self.sweeper.open(gain_db=gain, ppm=int(cfg["ppm"]))

        for band in enabled_bands:
            if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                return
            start_hz = int(band["start_hz"])
            end_hz = int(band["end_hz"])
            step_hz = int(band.get("step_hz", 12_500))
            bname = str(band["name"])
            centers = plan_band_centers(start_hz, end_hz)

            for center in centers:
                if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                    return
                with self.lock:
                    self.phase = "SWEEP"
                    self.sweep_band_name = bname
                    self.active_freq_hz = center
                    self.active_label = bname
                    self.status_text = f"Sweep {bname}: {fmt_mhz_short(center)} MHz"
                    self.error_text = ""

                freqs, psd_db, noise_floor = self.sweeper.capture_psd(center, burst_ms=68)
                peaks = self.sweeper.find_peaks_in_window(
                    freqs=freqs,
                    psd_db=psd_db,
                    noise_floor=noise_floor,
                    start_hz=start_hz,
                    end_hz=end_hz,
                    step_hz=step_hz,
                    threshold_db=float(cfg["threshold_db"]),
                    ignore_fn=self._is_ignored,
                )

                with self.lock:
                    self.sweep_center_hz = center
                    self.sweep_freqs = freqs
                    self.sweep_psd = psd_db
                    self.sweep_noise = noise_floor
                    self.sweep_peaks = peaks

                if peaks:
                    # Hand off RTL-SDR to dsd-fme to verify if candidate(s) are DMR
                    self.sweeper.close()
                    for peak in peaks[:3]:
                        if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                            return
                        self._verify_and_monitor(peak, bname)
                    if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                        return
                    # Re-open sweeper for remaining windows
                    self.sweeper.open(gain_db=gain, ppm=int(cfg["ppm"]))

    def _verify_and_monitor(self, peak: PeakCandidate, label: str) -> bool:
        """
        Launch dsd-fme on peak.freq_hz.
        If digital sync arrives within verify_sec -> stay until hang_sec after silence (or while HOLD).
        Otherwise -> stop dsd-fme, add temporary cooldown, return False.
        """
        with self.lock:
            cfg = dict(self.config)
            self.phase = "CHECK"
            self.active_freq_hz = peak.freq_hz
            self.active_label = self._lookup_channel_name(peak.freq_hz) or label
            self.active_snr_db = peak.snr_db
            self.status_text = f"Check {fmt_mhz(peak.freq_hz)} (+{peak.snr_db:.0f}dB)..."

        verify_sec = float(cfg["verify_sec"])
        hang_sec = float(cfg["hang_sec"])
        cooldown_sec = float(cfg["cooldown_sec"])

        self.dsd.start(
            freq_hz=peak.freq_hz,
            mode_flag=str(cfg["decoder_flag"]),
            gain_db=int(cfg["gain_db"]),
            ppm=int(cfg["ppm"]),
            bw_khz=int(cfg.get("bw_khz", 12)),
            squelch=0,
            vol=2,
            muted=False,
        )

        t_start = time.time()
        locked = False
        last_activity = t_start

        try:
            while not self._stop_evt.is_set() and not self._interrupt_evt.is_set():
                now = time.time()
                st = self.dsd.get_state()

                if st.error:
                    with self.lock:
                        self.error_text = st.error
                    break

                if st.synced or st.sync_count > 0:
                    if not locked:
                        locked = True
                        if cfg.get("auto_save_found", True):
                            self.save_favorite(
                                freq_hz=peak.freq_hz,
                                cc=st.color_code,
                                tg=st.slot1.tg or st.slot2.tg,
                                source="AUTO",
                            )
                    if st.last_sync_at > last_activity:
                        last_activity = st.last_sync_at
                    if st.last_voice_at > last_activity:
                        last_activity = st.last_voice_at
                    with self.lock:
                        self.phase = "LOCKED"
                        proto = st.protocol or "DMR"
                        self.status_text = f"{proto} LOCK {fmt_mhz(peak.freq_hz)} CC:{st.color_code or '-'}"

                if not locked:
                    if (now - t_start) >= verify_sec:
                        # No digital sync -> not DMR (probably analog FM/noise/carrier)
                        with self.lock:
                            self.cooldowns[peak.freq_hz] = now + cooldown_sec
                            self.skipped_count += 1
                            self.status_text = f"No DMR on {fmt_mhz(peak.freq_hz)} (skip {int(cooldown_sec)}s)"
                        break
                else:
                    with self.lock:
                        is_hold = self.hold
                    if not is_hold and (now - last_activity) >= hang_sec:
                        with self.lock:
                            self.status_text = f"Call ended on {fmt_mhz(peak.freq_hz)}"
                        break

                if not st.running:
                    break
                self._interrupt_evt.wait(0.1)
        finally:
            self.dsd.stop()

        return locked


# ==============================================================================
# CARDPUTER ZERO 320x170 FRAMEBUFFER UI
# ==============================================================================
class ZeroDmrApp:
    TABS = ["LIVE", "BANDS", "LIST", "LOG", "CFG"]

    def __init__(self, preview_out: str | None = None) -> None:
        self.preview_out = preview_out

        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.Surface((W, H), 0, 16, (0xF800, 0x07E0, 0x001F, 0))

        self.fb_fd = None
        if not self.preview_out:
            for fb_path in ("/dev/fb0", "/dev/fb1"):
                try:
                    self.fb_fd = os.open(fb_path, os.O_RDWR)
                    break
                except Exception:
                    pass

        self.kbd_fds: list[int] = []
        if not self.preview_out:
            kbd_paths = [KBD_DEV] if os.path.exists(KBD_DEV) else ["/dev/input/event3", "/dev/input/event0"]
            for p in kbd_paths:
                if os.path.exists(p):
                    try:
                        self.kbd_fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
                    except Exception:
                        pass

        def _f(path: str, sz: int, bold: bool = False):
            try:
                return pygame.font.Font(path, sz)
            except Exception:
                return pygame.font.SysFont("dejavusans", sz, bold=bold)

        self.f_xxs = _f(FONT_MONO, 8)
        self.f_xs = _f(FONT_REG, 9)
        self.f_xs_b = _f(FONT_BOLD, 9, True)
        self.f_sm = _f(FONT_REG, 10)
        self.f_sm_b = _f(FONT_BOLD, 10, True)
        self.f_md = _f(FONT_BOLD, 12, True)
        self.f_lg = _f(FONT_MONO_BOLD, 16, True)
        self.f_xl = _f(FONT_MONO_BOLD, 19, True)
        self.f_mono = _f(FONT_MONO, 9)
        self.f_mono_b = _f(FONT_MONO_BOLD, 10, True)

        self.running = True
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))

        self.audio = AudioControl()
        self.engine = ZeroDmrEngine()

        self.tab_idx = 0
        self.band_sel = 0
        self.list_sel = 0
        self.log_sel = 0
        self.cfg_sel = 0

        # Modals: None, "FREQ_INPUT", "BAND_EDIT", "ADD_CHAN"
        self.modal: str | None = None
        self.input_buf = ""
        self.band_edit_field = 0  # 0=start, 1=end, 2=step
        self.band_edit_vals = ["", "", ""]

        self.toast_msg = ""
        self.toast_until = 0.0
        self.esc_down_at: float | None = None
        self.shift_held = False
        self.ctrl_held = False

    def show_toast(self, msg: str, dur: float = 1.8) -> None:
        self.toast_msg = msg
        self.toast_until = time.time() + dur

    def flush_fb(self) -> None:
        if self.fb_fd is not None:
            try:
                os.lseek(self.fb_fd, 0, os.SEEK_SET)
                os.write(self.fb_fd, bytes(self.screen.get_buffer()))
            except Exception:
                pass

    # ==========================================================================
    # INPUT HANDLING (Cardputer Zero TCA8418 Normal + Shift + Sym + Fn + 3s ESC)
    # ==========================================================================
    def _decode_char(self, code: int) -> str:
        pair = EVDEV_CHAR_MAP.get(code)
        if not pair:
            return ""
        return pair[1] if self.shift_held else pair[0]

    def handle_events(self) -> bool:
        now = time.time()
        had_key = False
        if self.kbd_fds:
            r, _, _ = select.select(self.kbd_fds, [], [], 0.0)
            for fd in r:
                while True:
                    try:
                        data = os.read(fd, 24)
                        if len(data) < 24:
                            break
                        _, _, ev_type, code, value = struct.unpack("llHHi", data)
                        if ev_type != 1:
                            continue
                        had_key = True
                        if code in (KEY_LEFTSHIFT, KEY_RIGHTSHIFT):
                            self.shift_held = value != 0
                        elif code in (KEY_LEFTCTRL, KEY_RIGHTCTRL):
                            self.ctrl_held = value != 0
                        elif code == KEY_ESC:
                            if value == 1:
                                if self.modal is not None:
                                    self.esc_down_at = None
                                    self.modal = None
                                elif self.esc_down_at is None:
                                    self.esc_down_at = now
                            elif value == 0:
                                if self.esc_down_at is not None and (now - self.esc_down_at) < ESC_OVERLAY_DELAY:
                                    self._on_short_esc()
                                self.esc_down_at = None
                        elif value in (1, 2):
                            self.on_key(code, self._decode_char(code))
                    except BlockingIOError:
                        break
                    except Exception:
                        break

        if self.esc_down_at is not None and (now - self.esc_down_at) >= ESC_HOLD_SECONDS:
            self.running = False
        return had_key

    def _on_short_esc(self) -> None:
        if self.modal is not None:
            self.modal = None
        elif self.tab_idx != 0:
            self.tab_idx = 0
        else:
            self.show_toast(f"Hold ESC {int(ESC_HOLD_SECONDS)}s to exit ZeroDMR", 1.8)

    def on_key(self, code: int, ch: str = "") -> None:
        # Hardware Fn volume keys work everywhere (even inside modals):
        # FN+A = KEY_MUTE (113), FN+S = KEY_VOLUMEDOWN (114), FN+D = KEY_VOLUMEUP (115)
        if code == KEY_MUTE:
            m = self.audio.toggle_mute()
            self.show_toast("Audio MUTED" if m else f"Audio ON ({self.audio.volume_pct}%)")
            return
        if code == KEY_VOLUMEUP:
            self.audio.set_volume(self.audio.volume_pct + 5)
            self.show_toast(f"Volume: {self.audio.volume_pct}%")
            return
        if code == KEY_VOLUMEDOWN:
            self.audio.set_volume(self.audio.volume_pct - 5)
            self.show_toast(f"Volume: {self.audio.volume_pct}%")
            return

        if self.modal is not None:
            self._handle_modal_key(code, ch)
            return

        # Global tab shortcuts
        if code == KEY_TAB:
            self.tab_idx = (self.tab_idx + 1) % len(self.TABS)
            return
        if code == KEY_1:
            self.tab_idx = 0
            return
        if code == KEY_2:
            self.tab_idx = 1
            return
        if code == KEY_3:
            self.tab_idx = 2
            return
        if code == KEY_4:
            self.tab_idx = 3
            return
        if code == KEY_5:
            self.tab_idx = 4
            return

        # Global audio controls (works on both normal & Cardputer Sym layer +/-/=)
        if code == KEY_V:
            m = self.audio.toggle_mute()
            self.show_toast("Audio MUTED" if m else f"Audio ON ({self.audio.volume_pct}%)")
            return
        if ch in ("+", "=") or code in (KEY_EQUAL, 72, 73):
            self.audio.set_volume(self.audio.volume_pct + 10)
            self.show_toast(f"Volume: {self.audio.volume_pct}%")
            return
        if ch == "-" or code in (KEY_MINUS, 71):
            self.audio.set_volume(self.audio.volume_pct - 10)
            self.show_toast(f"Volume: {self.audio.volume_pct}%")
            return

        if self.tab_idx == 0:
            self._key_live(code)
        elif self.tab_idx == 1:
            self._key_bands(code)
        elif self.tab_idx == 2:
            self._key_list(code)
        elif self.tab_idx == 3:
            self._key_log(code)
        elif self.tab_idx == 4:
            self._key_cfg(code)

    def _key_live(self, code: int) -> None:
        if code == KEY_SPACE:
            res = self.engine.toggle_hold()
            mode = self.engine.config["mode"]
            if mode == "TUNER":
                self.show_toast("Paused" if res else "Resumed")
            else:
                self.show_toast("HOLD ON (stay on freq)" if res else "HOLD OFF (resuming scan)")
        elif code == KEY_M:
            order = ["AUTO", "LIST", "TUNER"]
            cur = self.engine.config["mode"]
            nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "AUTO"
            self.engine.set_mode(nxt)
            self.show_toast(f"Mode: {nxt}")
        elif code == KEY_S:
            st = self.engine.dsd.get_state()
            added = self.engine.save_favorite(cc=st.color_code, tg=st.slot1.tg or st.slot2.tg, source="FAV")
            self.show_toast("Saved to Favorites!" if added else "Updated Favorite!")
        elif code == KEY_B:
            f = self.engine.skip_current(permanent=False)
            self.show_toast(f"Skip {fmt_mhz(f)} (cooldown)")
        elif code == KEY_X:
            f = self.engine.skip_current(permanent=True)
            self.show_toast(f"Blacklisted {fmt_mhz(f)} MHz")
        elif code == KEY_F:
            self.modal = "FREQ_INPUT"
            self.input_buf = f"{self.engine.active_freq_hz / 1_000_000.0:.4f}"
        elif code == KEY_LEFT:
            self.engine.step_manual(-12_500)
        elif code == KEY_RIGHT:
            self.engine.step_manual(+12_500)
        elif code == KEY_UP:
            self.engine.step_manual(+100_000)
        elif code == KEY_DOWN:
            self.engine.step_manual(-100_000)

    def _key_bands(self, code: int) -> None:
        bands = self.engine.config["bands"]
        if code == KEY_UP:
            self.band_sel = (self.band_sel - 1) % len(bands)
        elif code == KEY_DOWN:
            self.band_sel = (self.band_sel + 1) % len(bands)
        elif code in (KEY_SPACE, KEY_ENTER):
            bands[self.band_sel]["enabled"] = not bands[self.band_sel].get("enabled", False)
            self.engine.notify_config_changed()
            st = "ON" if bands[self.band_sel]["enabled"] else "OFF"
            self.show_toast(f"{bands[self.band_sel]['name']}: {st}")
        elif code == KEY_A:
            self.engine.set_mode("AUTO")
            self.tab_idx = 0
            self.show_toast("Started AUTO Band Scan")
        elif code == KEY_E:
            b = bands[self.band_sel]
            self.modal = "BAND_EDIT"
            self.band_edit_field = 0
            self.band_edit_vals = [
                f"{b['start_hz'] / 1e6:.3f}",
                f"{b['end_hz'] / 1e6:.3f}",
                f"{b.get('step_hz', 12500) / 1e3:.2f}",
            ]

    def _key_list(self, code: int) -> None:
        chs = self.engine.channels
        if code == KEY_N:
            self.modal = "ADD_CHAN"
            self.input_buf = f"{self.engine.active_freq_hz / 1e6:.4f}"
            return
        if code == KEY_R:
            self.engine.set_mode("LIST")
            self.tab_idx = 0
            self.show_toast("Scanning Saved List")
            return
        if not chs:
            return
        self.list_sel = max(0, min(len(chs) - 1, self.list_sel))
        if code == KEY_UP:
            self.list_sel = (self.list_sel - 1) % len(chs)
        elif code == KEY_DOWN:
            self.list_sel = (self.list_sel + 1) % len(chs)
        elif code == KEY_ENTER:
            ch = chs[self.list_sel]
            self.engine.tune_manual(int(ch["freq_hz"]), ch.get("name", ""))
            self.tab_idx = 0
            self.show_toast(f"Tuned {fmt_mhz(int(ch['freq_hz']))} MHz")
        elif code == KEY_D:
            rem = chs.pop(self.list_sel)
            self.list_sel = max(0, min(len(chs) - 1, self.list_sel))
            self.engine.save_channels()
            self.show_toast(f"Deleted {fmt_mhz(int(rem['freq_hz']))}")
        elif code == KEY_B:
            ch = chs.pop(self.list_sel)
            self.engine.blacklist.add(int(ch["freq_hz"]))
            self.engine.save_channels()
            self.engine.save_blacklist()
            self.show_toast(f"Blacklisted {fmt_mhz(int(ch['freq_hz']))}")

    def _key_log(self, code: int) -> None:
        logs = list(self.engine.call_log)
        if code == KEY_C:
            self.engine.call_log.clear()
            self.engine.save_log()
            self.log_sel = 0
            self.show_toast("Call log cleared")
            return
        if not logs:
            return
        self.log_sel = max(0, min(len(logs) - 1, self.log_sel))
        if code == KEY_UP:
            self.log_sel = max(0, self.log_sel - 1)
        elif code == KEY_DOWN:
            self.log_sel = min(len(logs) - 1, self.log_sel + 1)
        elif code == KEY_ENTER:
            ev = logs[self.log_sel]
            self.engine.tune_manual(int(ev["freq_hz"]), f"TG:{ev.get('tg', '')}")
            self.tab_idx = 0
            self.show_toast(f"Tuned {fmt_mhz(int(ev['freq_hz']))} MHz")

    def _key_cfg(self, code: int) -> None:
        n_items = 10
        if code == KEY_UP:
            self.cfg_sel = (self.cfg_sel - 1) % n_items
            return
        if code == KEY_DOWN:
            self.cfg_sel = (self.cfg_sel + 1) % n_items
            return
        delta = 0
        if code in (KEY_RIGHT, KEY_ENTER, KEY_SPACE):
            delta = 1
        elif code == KEY_LEFT:
            delta = -1
        if delta == 0:
            return

        cfg = self.engine.config
        if self.cfg_sel == 0:  # Decoder Mode
            flags = [m[0] for m in DECODER_MODES]
            idx = flags.index(cfg["decoder_flag"]) if cfg["decoder_flag"] in flags else 0
            cfg["decoder_flag"] = flags[(idx + delta) % len(flags)]
        elif self.cfg_sel == 1:  # Gain
            g = int(cfg["gain_db"])
            idx = GAIN_OPTIONS.index(g) if g in GAIN_OPTIONS else 0
            cfg["gain_db"] = GAIN_OPTIONS[(idx + delta) % len(GAIN_OPTIONS)]
        elif self.cfg_sel == 2:  # RTL Bandwidth
            bw = int(cfg.get("bw_khz", 12))
            idx = BW_OPTIONS.index(bw) if bw in BW_OPTIONS else 1
            cfg["bw_khz"] = BW_OPTIONS[(idx + delta) % len(BW_OPTIONS)]
        elif self.cfg_sel == 3:  # PPM
            cfg["ppm"] = max(-80, min(80, int(cfg["ppm"]) + delta))
        elif self.cfg_sel == 4:  # Peak Threshold
            cfg["threshold_db"] = round(max(6.0, min(25.0, float(cfg["threshold_db"]) + delta * 1.0)), 1)
        elif self.cfg_sel == 5:  # Verify Timeout
            cfg["verify_sec"] = round(max(1.0, min(5.0, float(cfg["verify_sec"]) + delta * 0.2)), 1)
        elif self.cfg_sel == 6:  # Hang Time
            cfg["hang_sec"] = round(max(1.5, min(12.0, float(cfg["hang_sec"]) + delta * 0.5)), 1)
        elif self.cfg_sel == 7:  # Auto-Save Found
            cfg["auto_save_found"] = not cfg.get("auto_save_found", True)
        elif self.cfg_sel == 8:  # Volume
            self.audio.set_volume(self.audio.volume_pct + delta * 5)
            return
        elif self.cfg_sel == 9:  # Clear Blacklist & Cooldowns
            if code in (KEY_ENTER, KEY_SPACE):
                self.engine.blacklist.clear()
                self.engine.cooldowns.clear()
                self.engine.save_blacklist()
                self.show_toast("Blacklist & skips cleared!")
            return
        self.engine.notify_config_changed()

    def _handle_modal_key(self, code: int, ch: str = "") -> None:
        # Accept '.' from Cardputer Sym layer (code 91 -> '.'), ',' (code 90 -> ','), or '.' key
        digit_ch = "." if ch in (".", ",") else (ch if ch in "0123456789" else "")

        if self.modal in ("FREQ_INPUT", "ADD_CHAN"):
            if code in (KEY_BACKSPACE, KEY_DELETE):
                self.input_buf = self.input_buf[:-1]
            elif digit_ch and len(self.input_buf) < 10:
                if digit_ch == "." and "." in self.input_buf:
                    return
                self.input_buf += digit_ch
            elif code == KEY_ENTER:
                try:
                    mhz = float(self.input_buf)
                    if 24.0 <= mhz <= 1700.0:
                        hz = int(round(mhz * 1_000_000))
                        if self.modal == "FREQ_INPUT":
                            self.engine.tune_manual(hz)
                            self.show_toast(f"Tuned {fmt_mhz(hz)} MHz")
                        else:
                            self.engine.save_favorite(freq_hz=hz, source="FAV")
                            self.show_toast(f"Added {fmt_mhz(hz)} MHz")
                        self.modal = None
                    else:
                        self.show_toast("Range: 24..1700 MHz")
                except ValueError:
                    self.show_toast("Invalid frequency")
        elif self.modal == "BAND_EDIT":
            if code in (KEY_TAB, KEY_DOWN):
                self.band_edit_field = (self.band_edit_field + 1) % 3
            elif code == KEY_UP:
                self.band_edit_field = (self.band_edit_field - 1) % 3
            elif code in (KEY_BACKSPACE, KEY_DELETE):
                self.band_edit_vals[self.band_edit_field] = self.band_edit_vals[self.band_edit_field][:-1]
            elif digit_ch and len(self.band_edit_vals[self.band_edit_field]) < 9:
                cur = self.band_edit_vals[self.band_edit_field]
                if digit_ch == "." and "." in cur:
                    return
                self.band_edit_vals[self.band_edit_field] = cur + digit_ch
            elif code == KEY_ENTER:
                try:
                    s_hz = int(round(float(self.band_edit_vals[0]) * 1e6))
                    e_hz = int(round(float(self.band_edit_vals[1]) * 1e6))
                    st_hz = int(round(float(self.band_edit_vals[2]) * 1e3))
                    if 24_000_000 <= s_hz < e_hz <= 1_700_000_000 and 2_500 <= st_hz <= 100_000:
                        b = self.engine.config["bands"][self.band_sel]
                        b["start_hz"] = s_hz
                        b["end_hz"] = e_hz
                        b["step_hz"] = st_hz
                        b["enabled"] = True
                        self.engine.notify_config_changed()
                        self.modal = None
                        self.show_toast("Band updated!")
                    else:
                        self.show_toast("Check Start < End & Step")
                except ValueError:
                    self.show_toast("Invalid numbers")

    # ==========================================================================
    # RENDERING
    # ==========================================================================
    def _pill(self, x: int, y: int, text: str, fg, bg, font=None) -> int:
        f = font or self.f_xxs
        surf = f.render(text, True, fg)
        pw = surf.get_width() + 8
        ph = surf.get_height() + 4
        pygame.draw.rect(self.screen, bg, (x, y, pw, ph), border_radius=3)
        self.screen.blit(surf, (x + 4, y + 2))
        return pw

    def draw_header(self) -> None:
        pygame.draw.rect(self.screen, SURFACE, (0, 0, W, 20))
        pygame.draw.line(self.screen, BORDER, (0, 19), (W, 19))

        # Tabs
        tx = 4
        for idx, name in enumerate(self.TABS):
            active = idx == self.tab_idx
            col = MINT if active else SUBTEXT
            lbl = self.f_xs_b.render(f"{idx+1}:{name}", True, col)
            if active:
                pygame.draw.rect(self.screen, MINT_DARK, (tx - 2, 2, lbl.get_width() + 6, 15), border_radius=3)
                pygame.draw.line(self.screen, MINT, (tx - 1, 18), (tx + lbl.get_width() + 3, 18), 2)
            self.screen.blit(lbl, (tx + 1, 4))
            tx += lbl.get_width() + 8

        # Right status pill
        with self.engine.lock:
            phase = self.engine.phase
            hold = self.engine.hold
        if self.audio.muted:
            m_lbl = self.f_xxs.render("M", True, DANGER)
            pygame.draw.rect(self.screen, DANGER_DARK, (W - 58, 3, 12, 13), border_radius=2)
            self.screen.blit(m_lbl, (W - 55, 5))

        if phase == "LOCKED":
            p_txt, p_fg, p_bg = ("HOLD" if hold else "DMR"), (WARN if hold else GREEN), (WARN_DARK if hold else GREEN_DARK)
        elif phase == "CHECK":
            p_txt, p_fg, p_bg = "CHK", WARN, WARN_DARK
        elif phase == "SWEEP":
            p_txt, p_fg, p_bg = "SCAN", CYAN, CYAN_DARK
        elif phase == "TUNER":
            p_txt, p_fg, p_bg = "TUNE", MINT, MINT_DARK
        elif phase == "ERROR":
            p_txt, p_fg, p_bg = "ERR", DANGER, DANGER_DARK
        else:
            p_txt, p_fg, p_bg = "IDLE", SUBTEXT, ELEVATED

        surf = self.f_xxs.render(p_txt, True, p_fg)
        pw = max(36, surf.get_width() + 8)
        pygame.draw.rect(self.screen, p_bg, (W - pw - 4, 3, pw, 13), border_radius=3)
        self.screen.blit(surf, (W - pw - 4 + (pw - surf.get_width()) // 2, 5))

    def draw_footer(self, hint: str) -> None:
        pygame.draw.rect(self.screen, SURFACE, (0, H - 14, W, 14))
        pygame.draw.line(self.screen, BORDER, (0, H - 14), (W, H - 14))
        surf = self.f_xxs.render(hint[:52], True, SUBTEXT)
        self.screen.blit(surf, (5, H - 11))

    def draw_live(self) -> None:
        with self.engine.lock:
            mode = self.engine.config["mode"]
            phase = self.engine.phase
            freq_hz = self.engine.active_freq_hz
            label = self.engine.active_label
            snr = self.engine.active_snr_db
            status = self.engine.status_text
            hold = self.engine.hold
            freqs = self.engine.sweep_freqs
            psd = self.engine.sweep_psd
            nf = self.engine.sweep_noise
            peaks = list(self.engine.sweep_peaks)
            thr = float(self.engine.config["threshold_db"])
            skipped = self.engine.skipped_count
            found = self.engine.found_count

        dsd = self.engine.dsd.get_state()

        # Top Frequency Card (y=22..52)
        pygame.draw.rect(self.screen, SURFACE, (4, 22, W - 8, 30), border_radius=4)
        pygame.draw.rect(self.screen, MINT if phase == "LOCKED" else BORDER, (4, 22, W - 8, 30), 1, border_radius=4)

        f_txt = self.f_xl.render(fmt_mhz(freq_hz), True, TEXT)
        self.screen.blit(f_txt, (10, 26))
        u_txt = self.f_xs_b.render("MHz", True, MINT)
        self.screen.blit(u_txt, (10 + f_txt.get_width() + 3, 35))

        # Mode pill + label on right of freq card
        m_col = MINT if mode == "AUTO" else (CYAN if mode == "LIST" else WARN)
        mw = self._pill(W - 46, 25, mode, m_col, ELEVATED)
        lbl_clean = (label or "Ready")[:22]
        l_surf = self.f_xs.render(lbl_clean, True, SUBTEXT)
        self.screen.blit(l_surf, (W - 8 - l_surf.get_width(), 40))

        # If we have DMR lock or are in TUNER mode with DSD running, show Slot Cards!
        show_dmr_panel = (phase == "LOCKED") or (mode == "TUNER" and dsd.running)
        if show_dmr_panel:
            self._draw_dmr_cards(dsd, hold, status)
        else:
            self._draw_spectrum_panel(freqs, psd, nf, thr, peaks, phase, status, snr, found, skipped)

        self.draw_footer("SPC:Hold M:Mode F:Freq S:Save B:Skip X:Blk V:Mute")

    def _draw_dmr_cards(self, dsd: DsdState, hold: bool, status: str) -> None:
        # Meta bar (y=55..70)
        proto = dsd.protocol or "DMR"
        sub = f" {dsd.submode}" if dsd.submode else ""
        cc = dsd.color_code if dsd.color_code else "-"
        x = 6
        x += self._pill(x, 55, f"{proto}{sub}", GREEN if dsd.synced else SUBTEXT, GREEN_DARK if dsd.synced else ELEVATED) + 4
        x += self._pill(x, 55, f"CC:{cc}", CYAN, CYAN_DARK) + 4
        enc = dsd.slot1.encrypted or dsd.slot2.encrypted
        x += self._pill(x, 55, "ENC" if enc else "CLEAR", DANGER if enc else MINT, DANGER_DARK if enc else MINT_DARK) + 4
        if hold:
            self._pill(x, 55, "HOLD", WARN, WARN_DARK)

        # Two Slot Cards (y=72..134)
        for idx, slot in enumerate((dsd.slot1, dsd.slot2)):
            cx = 6 + idx * 156
            cw = 152
            cy = 72
            ch = 62
            is_v = slot.voice
            border_col = GREEN if is_v else (MINT_DIM if slot.active else BORDER)
            bg_col = (12, 34, 24) if is_v else SURFACE
            pygame.draw.rect(self.screen, bg_col, (cx, cy, cw, ch), border_radius=4)
            pygame.draw.rect(self.screen, border_col, (cx, cy, cw, ch), 1, border_radius=4)

            # Slot header
            s_lbl = self.f_xs_b.render(f"SLOT {idx + 1}", True, GREEN if is_v else SUBTEXT)
            self.screen.blit(s_lbl, (cx + 6, cy + 4))
            if is_v:
                self._pill(cx + cw - 42, cy + 3, "VOICE", GREEN, GREEN_DARK)
            elif slot.call_type:
                self._pill(cx + cw - 38, cy + 3, slot.call_type[:5], CYAN, CYAN_DARK)

            # TG
            tg_str = f"TG {slot.tg}" if slot.tg else "TG ---"
            tg_surf = self.f_md.render(tg_str[:14], True, TEXT if slot.tg else MUTED)
            self.screen.blit(tg_surf, (cx + 6, cy + 20))

            # SRC
            src_str = f"ID: {slot.src}" if slot.src else "ID: ---"
            src_surf = self.f_mono.render(src_str[:18], True, CYAN if slot.src else MUTED)
            self.screen.blit(src_surf, (cx + 6, cy + 37))

            if slot.talker_alias:
                ta = self.f_xxs.render(slot.talker_alias[:20], True, WARN)
                self.screen.blit(ta, (cx + 6, cy + 50))
            elif slot.encrypted:
                en = self.f_xxs.render(f"ENC {slot.enc_label}", True, DANGER)
                self.screen.blit(en, (cx + 6, cy + 50))

        # Raw DSD status ticker at bottom (y=137..152)
        pygame.draw.rect(self.screen, ELEVATED, (6, 137, W - 12, 15), border_radius=3)
        raw_txt = dsd.raw_lines[-1] if dsd.raw_lines else status
        rs = self.f_xxs.render(raw_txt[:54], True, SUBTEXT)
        self.screen.blit(rs, (10, 140))

    def _draw_spectrum_panel(
        self,
        freqs: np.ndarray | None,
        psd: np.ndarray | None,
        nf: float,
        thr: float,
        peaks: list[PeakCandidate],
        phase: str,
        status: str,
        snr: float,
        found: int,
        skipped: int,
    ) -> None:
        px, py, pw, ph = 6, 56, W - 12, 74
        pygame.draw.rect(self.screen, SURFACE, (px, py, pw, ph), border_radius=4)
        pygame.draw.rect(self.screen, BORDER, (px, py, pw, ph), 1, border_radius=4)

        if freqs is not None and psd is not None and len(psd) > 8:
            # Fast vectorized max-binning so narrow peaks stay visible at low CPU cost
            cols = pw - 4
            n_bins = len(psd)
            if n_bins >= cols:
                usable = (n_bins // cols) * cols
                col_vals = psd[:usable].reshape(cols, -1).max(axis=1)
            else:
                col_vals = np.interp(np.linspace(0, n_bins - 1, cols), np.arange(n_bins), psd)
            y_min = nf - 5.0
            y_max = max(nf + thr + 22.0, float(np.max(col_vals)) + 3.0)
            span = max(10.0, y_max - y_min)

            # Horizontal grid + threshold line
            thr_y = py + ph - 12 - int(((nf + thr - y_min) / span) * (ph - 16))
            thr_y = max(py + 3, min(py + ph - 13, thr_y))
            for gx in range(px + 2, px + pw - 2, 6):
                pygame.draw.line(self.screen, WARN_DARK, (gx, thr_y), (min(gx + 3, px + pw - 3), thr_y))

            # Draw spectrum bars
            base_y = py + ph - 12
            for i in range(cols):
                v = col_vals[i]
                h_px = int(max(1, min(ph - 16, ((v - y_min) / span) * (ph - 16))))
                x = px + 2 + i
                above = v >= (nf + thr)
                col = MINT if above else (38, 118, 158)
                pygame.draw.line(self.screen, col, (x, base_y), (x, base_y - h_px))

            # Peak markers
            f0, f1 = float(freqs[0]), float(freqs[-1])
            fspan = max(1.0, f1 - f0)
            for pk in peaks[:5]:
                rx = px + 2 + int(((pk.freq_hz - f0) / fspan) * cols)
                if px + 3 <= rx <= px + pw - 3:
                    pygame.draw.polygon(self.screen, WARN, [(rx, py + 9), (rx - 3, py + 3), (rx + 3, py + 3)])

            # Frequency axis labels
            l_lo = self.f_xxs.render(f"{f0 / 1e6:.2f}", True, SUBTEXT)
            l_mi = self.f_xxs.render(f"{(f0 + f1) / 2e6:.2f} MHz", True, SUBTEXT)
            l_hi = self.f_xxs.render(f"{f1 / 1e6:.2f}", True, SUBTEXT)
            self.screen.blit(l_lo, (px + 4, py + ph - 10))
            self.screen.blit(l_mi, (px + pw // 2 - l_mi.get_width() // 2, py + ph - 10))
            self.screen.blit(l_hi, (px + pw - l_hi.get_width() - 4, py + ph - 10))
        else:
            msg = "Waiting for RTL-SDR spectrum..." if phase != "ERROR" else status
            ms = self.f_sm.render(msg[:42], True, DANGER if phase == "ERROR" else SUBTEXT)
            self.screen.blit(ms, (px + (pw - ms.get_width()) // 2, py + ph // 2 - 6))

        # Bottom live status bar (y=134..152)
        pygame.draw.rect(self.screen, ELEVATED, (6, 134, W - 12, 17), border_radius=3)
        st_col = WARN if phase == "CHECK" else (DANGER if phase == "ERROR" else TEXT)
        s_surf = self.f_xxs.render(status[:39], True, st_col)
        self.screen.blit(s_surf, (10, 138))
        cnt = self.f_xxs.render(f"DMR:{found} SKP:{skipped}", True, MINT)
        self.screen.blit(cnt, (W - 10 - cnt.get_width(), 138))

    def draw_bands(self) -> None:
        bands = self.engine.config["bands"]
        vis = 6
        start = max(0, min(len(bands) - vis, self.band_sel - vis // 2))
        y = 24
        for i in range(start, min(len(bands), start + vis)):
            b = bands[i]
            sel = i == self.band_sel
            en = bool(b.get("enabled", False))
            pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (4, y, W - 8, 20), border_radius=3)
            if sel:
                pygame.draw.rect(self.screen, MINT, (4, y, W - 8, 20), 1, border_radius=3)

            chk = "[x]" if en else "[ ]"
            c_surf = self.f_mono_b.render(chk, True, MINT if en else MUTED)
            self.screen.blit(c_surf, (8, y + 4))

            n_surf = self.f_xs_b.render(b["name"][:18], True, TEXT if en else SUBTEXT)
            self.screen.blit(n_surf, (34, y + 5))

            rng = f"{b['start_hz']/1e6:.2f}-{b['end_hz']/1e6:.2f}"
            r_surf = self.f_xxs.render(rng, True, CYAN if en else MUTED)
            self.screen.blit(r_surf, (W - 10 - r_surf.get_width(), y + 6))
            y += 21

        self.draw_footer("SPC/ENT:Toggle  E:Edit Band  A:Start AUTO Scan")

    def draw_list(self) -> None:
        chs = self.engine.channels
        if not chs:
            msg = self.f_sm.render("No channels yet. Press N to add.", True, SUBTEXT)
            self.screen.blit(msg, (W // 2 - msg.get_width() // 2, 76))
        else:
            self.list_sel = max(0, min(len(chs) - 1, self.list_sel))
            vis = 6
            start = max(0, min(max(0, len(chs) - vis), self.list_sel - vis // 2))
            y = 24
            for i in range(start, min(len(chs), start + vis)):
                ch = chs[i]
                sel = i == self.list_sel
                pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (4, y, W - 8, 20), border_radius=3)
                if sel:
                    pygame.draw.rect(self.screen, MINT, (4, y, W - 8, 20), 1, border_radius=3)

                f_s = self.f_mono_b.render(fmt_mhz(int(ch["freq_hz"])), True, TEXT)
                self.screen.blit(f_s, (8, y + 4))

                name = str(ch.get("name", ""))[:15]
                n_s = self.f_xs.render(name, True, SUBTEXT)
                self.screen.blit(n_s, (78, y + 5))

                meta = ""
                if ch.get("cc"):
                    meta += f"CC{ch['cc']} "
                if ch.get("last_tg"):
                    meta += f"TG{ch['last_tg']}"
                if not meta:
                    meta = str(ch.get("source", ""))
                m_s = self.f_xxs.render(meta[:14], True, MINT if ch.get("source") == "AUTO" else CYAN)
                self.screen.blit(m_s, (W - 10 - m_s.get_width(), y + 6))
                y += 21

        self.draw_footer("ENT:Tune  R:Scan List  N:Add  D:Del  B:Blacklist")

    def draw_log(self) -> None:
        logs = list(self.engine.call_log)
        if not logs:
            msg = self.f_sm.render("No DMR calls decoded yet.", True, SUBTEXT)
            self.screen.blit(msg, (W // 2 - msg.get_width() // 2, 76))
        else:
            self.log_sel = max(0, min(len(logs) - 1, self.log_sel))
            vis = 6
            start = max(0, min(max(0, len(logs) - vis), self.log_sel - vis // 2))
            y = 24
            for i in range(start, min(len(logs), start + vis)):
                ev = logs[i]
                sel = i == self.log_sel
                pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (4, y, W - 8, 20), border_radius=3)
                if sel:
                    pygame.draw.rect(self.screen, MINT, (4, y, W - 8, 20), 1, border_radius=3)

                ts = datetime.datetime.fromtimestamp(ev.get("ts", 0)).strftime("%H:%M:%S")
                t_s = self.f_xxs.render(ts, True, MUTED)
                self.screen.blit(t_s, (8, y + 6))

                fr = fmt_mhz(int(ev.get("freq_hz", 0)))
                f_s = self.f_mono.render(fr, True, TEXT)
                self.screen.blit(f_s, (54, y + 5))

                sl = f"S{ev.get('slot', 1)}"
                tg = f"TG:{ev.get('tg', '-')}"
                src = f"ID:{ev.get('src', '-')}"
                d_s = self.f_xxs.render(f"{sl} {tg} {src}"[:24], True, GREEN if not ev.get("encrypted") else DANGER)
                self.screen.blit(d_s, (118, y + 6))
                y += 21

        self.draw_footer("UP/DN:Scroll  ENT:Tune to Freq  C:Clear Log")

    def draw_cfg(self) -> None:
        cfg = self.engine.config
        mode_name = dict(DECODER_MODES).get(cfg["decoder_flag"], cfg["decoder_flag"])
        gain_str = "AUTO" if int(cfg["gain_db"]) == 0 else f"{int(cfg['gain_db'])} dB"
        items = [
            ("Decoder Mode", mode_name),
            ("RTL Gain", gain_str),
            ("RTL Bandwidth", f"{int(cfg.get('bw_khz', 12))} kHz"),
            ("PPM Correction", f"{int(cfg['ppm']):+d} ppm"),
            ("Peak Threshold", f"+{float(cfg['threshold_db']):.0f} dB"),
            ("DMR Verify Time", f"{float(cfg['verify_sec']):.1f} s"),
            ("Call Hang Time", f"{float(cfg['hang_sec']):.1f} s"),
            ("Auto-Save DMR", "ON" if cfg.get("auto_save_found", True) else "OFF"),
            ("Audio Volume", f"{self.audio.volume_pct}%" + (" (MUTE)" if self.audio.muted else "")),
            ("Clear Blacklist", f"{len(self.engine.blacklist)} blk / {len(self.engine.cooldowns)} skip"),
        ]
        vis = 6
        start = max(0, min(len(items) - vis, self.cfg_sel - vis // 2))
        y = 24
        for i in range(start, min(len(items), start + vis)):
            k, v = items[i]
            sel = i == self.cfg_sel
            pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (4, y, W - 8, 20), border_radius=3)
            if sel:
                pygame.draw.rect(self.screen, MINT, (4, y, W - 8, 20), 1, border_radius=3)
            ks = self.f_xs_b.render(k, True, TEXT if sel else SUBTEXT)
            vs = self.f_mono_b.render(v, True, MINT if sel else CYAN)
            self.screen.blit(ks, (10, y + 5))
            self.screen.blit(vs, (W - 10 - vs.get_width(), y + 4))
            y += 21

        self.draw_footer("UP/DN:Select  LEFT/RIGHT:Change  ENT:Action")

    def draw_modal(self) -> None:
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        self.screen.blit(overlay, (0, 0))

        if self.modal in ("FREQ_INPUT", "ADD_CHAN"):
            bx, by, bw, bh = 36, 44, 248, 80
            pygame.draw.rect(self.screen, SURFACE, (bx, by, bw, bh), border_radius=6)
            pygame.draw.rect(self.screen, MINT, (bx, by, bw, bh), 1, border_radius=6)
            title = "Direct Tune (MHz)" if self.modal == "FREQ_INPUT" else "Add Channel (MHz)"
            self.screen.blit(self.f_sm_b.render(title, True, MINT), (bx + 12, by + 10))
            pygame.draw.rect(self.screen, BG, (bx + 12, by + 30, bw - 24, 26), border_radius=4)
            pygame.draw.rect(self.screen, BORDER, (bx + 12, by + 30, bw - 24, 26), 1, border_radius=4)
            val = self.f_lg.render(self.input_buf + "_", True, TEXT)
            self.screen.blit(val, (bx + 18, by + 34))
            self.screen.blit(self.f_xxs.render("0-9  SYM+.   ENTER:OK   ESC:Cancel", True, SUBTEXT), (bx + 12, by + 63))

        elif self.modal == "BAND_EDIT":
            bx, by, bw, bh = 28, 28, 264, 114
            pygame.draw.rect(self.screen, SURFACE, (bx, by, bw, bh), border_radius=6)
            pygame.draw.rect(self.screen, MINT, (bx, by, bw, bh), 1, border_radius=6)
            bname = self.engine.config["bands"][self.band_sel]["name"]
            self.screen.blit(self.f_sm_b.render(f"Edit: {bname[:22]}", True, MINT), (bx + 10, by + 8))
            labels = ["Start (MHz):", "End (MHz):  ", "Step (kHz): "]
            for idx, lbl in enumerate(labels):
                ry = by + 28 + idx * 22
                active = idx == self.band_edit_field
                self.screen.blit(self.f_xs.render(lbl, True, TEXT if active else SUBTEXT), (bx + 12, ry + 3))
                pygame.draw.rect(self.screen, BG, (bx + 92, ry, 158, 18), border_radius=3)
                pygame.draw.rect(self.screen, MINT if active else BORDER, (bx + 92, ry, 158, 18), 1, border_radius=3)
                txt = self.band_edit_vals[idx] + ("_" if active else "")
                self.screen.blit(self.f_mono_b.render(txt, True, TEXT), (bx + 98, ry + 3))
            self.screen.blit(self.f_xxs.render("TAB/UP/DN:Field  ENT:Save  ESC:Cancel", True, SUBTEXT), (bx + 10, by + 98))

    def draw_toast(self) -> None:
        if self.toast_msg and time.time() < self.toast_until:
            surf = self.f_xs_b.render(self.toast_msg[:42], True, TEXT)
            tw = surf.get_width() + 16
            th = 18
            tx = (W - tw) // 2
            ty = H - 36
            pygame.draw.rect(self.screen, ELEVATED, (tx, ty, tw, th), border_radius=4)
            pygame.draw.rect(self.screen, MINT, (tx, ty, tw, th), 1, border_radius=4)
            self.screen.blit(surf, (tx + 8, ty + 4))

    def draw_esc_overlay(self) -> None:
        if self.esc_down_at is None:
            return
        elapsed = time.time() - self.esc_down_at
        if elapsed < ESC_OVERLAY_DELAY:
            return
        pct = min(1.0, elapsed / ESC_HOLD_SECONDS)
        rem = max(0.0, ESC_HOLD_SECONDS - elapsed)
        bx, by, bw, bh = 45, 62, 230, 46
        pygame.draw.rect(self.screen, SURFACE, (bx, by, bw, bh), border_radius=6)
        pygame.draw.rect(self.screen, DANGER, (bx, by, bw, bh), 1, border_radius=6)
        lbl = self.f_sm_b.render(f"HOLD ESC TO EXIT... {rem:.1f}s", True, TEXT)
        self.screen.blit(lbl, (bx + (bw - lbl.get_width()) // 2, by + 8))
        bar_x, bar_y, bar_w, bar_h = bx + 14, by + 28, bw - 28, 8
        pygame.draw.rect(self.screen, BG, (bar_x, bar_y, bar_w, bar_h), border_radius=3)
        fill_w = max(2, int(bar_w * pct))
        pygame.draw.rect(self.screen, DANGER, (bar_x, bar_y, fill_w, bar_h), border_radius=3)

    def render(self) -> None:
        self.screen.fill(BG)
        self.draw_header()
        if self.tab_idx == 0:
            self.draw_live()
        elif self.tab_idx == 1:
            self.draw_bands()
        elif self.tab_idx == 2:
            self.draw_list()
        elif self.tab_idx == 3:
            self.draw_log()
        elif self.tab_idx == 4:
            self.draw_cfg()
        if self.modal is not None:
            self.draw_modal()
        self.draw_toast()
        self.draw_esc_overlay()

    def run(self) -> None:
        if self.preview_out:
            self.render()
            pygame.image.save(self.screen, self.preview_out)
            return

        self.engine.start()
        last_render = 0.0
        try:
            while self.running:
                t0 = time.time()
                had_key = self.handle_events()
                # Lower render rate (5 FPS) while decoding voice so dsd-fme gets max CPU;
                # redraw immediately on keypress or during ESC-hold overlay.
                with self.engine.lock:
                    phase = self.engine.phase
                min_interval = 0.06 if (had_key or self.esc_down_at is not None) else (0.18 if phase == "LOCKED" else 0.10)
                if had_key or (t0 - last_render) >= min_interval:
                    self.render()
                    self.flush_fb()
                    last_render = t0
                dt = time.time() - t0
                if dt < 0.04:
                    time.sleep(0.04 - dt)
        finally:
            self.engine.stop()
            for fd in self.kbd_fds:
                try:
                    os.close(fd)
                except Exception:
                    pass
            if self.fb_fd is not None:
                try:
                    os.close(self.fb_fd)
                except Exception:
                    pass


if __name__ == "__main__":
    out_path = sys.argv[2] if len(sys.argv) >= 3 and sys.argv[1] == "--preview" else None
    ZeroDmrApp(preview_out=out_path).run()
