#!/usr/bin/env python3
"""
ZeroWave — 2m/70cm All-Mode RTL-SDR Scanner, Digital Voice & Telemetry Suite
for M5Stack Cardputer Zero (320x170 RGB565 Framebuffer + TCA8418 Evdev Keyboard).

Modes (Quick-Switch Grid on 'Q'):
  1. VOICE  : DMR / YSF / D-STAR / M17 / dPMR / NXDN / P25 / Auto (-fa) + Callsign DB + WAV Rec
  2. ANALOG : NFM / AM (Airband) / WFM + Band Scan + WAV Rec
  3. PAGER  : POCSAG 512/1200/2400 & FLEX (DAPNET)
  4. APRS   : AFSK1200 Packet Radio (144.800 EU / 145.825 ISS)
  5. ISM433 : 433/868 MHz Sensors, Weather & TPMS (rtl_433)
  6. SONDE  : 400-406 MHz Radiosonde Sweep & Vaisala RS41 Telemetry
"""

from __future__ import annotations

import collections
import datetime
import json
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

from aux_decoders import AnalogRunner, AprsRunner, Ism433Runner, PagerRunner, SondeRunner
from dsd_runner import DsdRunner, DsdState
from radio_db import RadioDatabase
from rtlsdr_sweep import PeakCandidate, RtlSdrError, RtlSdrSweeper, plan_band_centers

# ==============================================================================
# DISPLAY & VICE-CYBERPUNK MINIMAL THEME
# ==============================================================================
APP_NAME = "ZeroWave"
W, H = 320, 170

BG = (7, 8, 14)
SURFACE = (15, 18, 28)
ELEVATED = (23, 28, 42)
BORDER = (42, 48, 68)
MINT = (0, 232, 198)          # Electric Vice Cyan-Mint
MINT_DIM = (16, 138, 124)
MINT_DARK = (9, 44, 46)
CYAN = (72, 206, 255)
CYAN_DARK = (14, 46, 68)
PINK = (255, 76, 158)         # Subtle GTA-6 Sunset Pink Accent
PINK_DARK = (62, 16, 40)
TEXT = (246, 248, 254)
SUBTEXT = (148, 158, 178)
MUTED = (86, 94, 114)
DANGER = (255, 82, 92)
DANGER_DARK = (66, 16, 22)
WARN = (255, 184, 62)
WARN_DARK = (64, 42, 10)
GREEN = (56, 230, 132)
GREEN_DARK = (12, 52, 30)

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
KBD_DEV = "/dev/input/by-path/platform-3f804000.i2c-event"

CONFIG_DIR = os.path.expanduser("~/.config/zerowave")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
CHANNELS_FILE = os.path.join(CONFIG_DIR, "channels.json")
BLACKLIST_FILE = os.path.join(CONFIG_DIR, "blacklist.json")
LOG_FILE = os.path.join(CONFIG_DIR, "event_log.json")
DB_FILE = os.path.join(CONFIG_DIR, "radiodb.sqlite")
REC_DIR = os.path.expanduser("~/ZeroWave_Recordings")

# ==============================================================================
# EVDEV KEYCODES & CARDPUTER ZERO FULL KEYMAP
# ==============================================================================
ESC_HOLD_SECONDS = 3.0
ESC_OVERLAY_DELAY = 0.45

KEY_ESC = 1
KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6 = 2, 3, 4, 5, 6, 7
KEY_7, KEY_8, KEY_9, KEY_0 = 8, 9, 10, 11
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
# APP MODES & PRESETS (2m / 70cm Antenna Coverage)
# ==============================================================================
APP_MODES = [
    ("VOICE", "VOICE", "DMR/YSF/M17", MINT),
    ("ANALOG", "ANALOG", "NFM/AM/WFM", PINK),
    ("PAGER", "PAGER", "POCSAG/DAP", WARN),
    ("APRS", "APRS", "144.8 / ISS", CYAN),
    ("ISM433", "ISM 433", "Sensors/TPMS", GREEN),
    ("SONDE", "SONDE", "RS41 Balloon", (180, 130, 255)),
]

DECODER_MODES = [
    ("-fs", "DMR Only (-fs)"),
    ("-fa", "Auto Digital (-fa)"),
    ("-fy", "YSF C4FM (-fy)"),
    ("-fd", "D-STAR (-fd)"),
    ("-fz", "M17 Codec2 (-fz)"),
    ("-fm", "dPMR (-fm)"),
    ("-fi", "NXDN48 (-fi)"),
    ("-fn", "NXDN96 (-fn)"),
    ("-f1", "P25 Ph1 (-f1)"),
    ("-ft", "DMR/P25 Trunk (-ft)"),
]

GAIN_OPTIONS = [0, 16, 24, 32, 36, 42, 48]  # 0 = Auto AGC
BW_OPTIONS = [8, 12, 16, 24]
ANALOG_MODS = ["NFM", "AM", "WFM"]

DEFAULT_VOICE_BANDS = [
    {"id": "ham70_rep", "name": "70cm Ham DMR/DV", "start_hz": 438_200_000, "end_hz": 439_425_000, "step_hz": 12_500, "enabled": True},
    {"id": "ham70_sim", "name": "70cm Ham Simplex", "start_hz": 433_400_000, "end_hz": 433_600_000, "step_hz": 12_500, "enabled": True},
    {"id": "pmr446_d", "name": "PMR446 Digital", "start_hz": 446_100_000, "end_hz": 446_200_000, "step_hz": 6_250, "enabled": True},
    {"id": "ham70_lo", "name": "70cm Ham 430-431", "start_hz": 430_000_000, "end_hz": 431_000_000, "step_hz": 12_500, "enabled": False},
    {"id": "ham2m", "name": "2m Ham Digital", "start_hz": 145_575_000, "end_hz": 145_800_000, "step_hz": 12_500, "enabled": False},
    {"id": "uhf_440", "name": "UHF 440-450 MHz", "start_hz": 440_000_000, "end_hz": 450_000_000, "step_hz": 12_500, "enabled": False},
    {"id": "uhf_450", "name": "UHF 450-460 MHz", "start_hz": 450_000_000, "end_hz": 460_000_000, "step_hz": 12_500, "enabled": False},
    {"id": "uhf_460", "name": "UHF 460-470 MHz", "start_hz": 460_000_000, "end_hz": 470_000_000, "step_hz": 12_500, "enabled": False},
    {"id": "custom", "name": "Custom Band", "start_hz": 438_000_000, "end_hz": 440_000_000, "step_hz": 12_500, "enabled": False},
]

DEFAULT_ANALOG_BANDS = [
    {"id": "wfm_bc", "name": "FM Broadcast (WFM)", "freq_hz": 100_000_000, "start_hz": 87_500_000, "end_hz": 108_000_000, "step_hz": 100_000, "mod": "WFM", "enabled": True},
    {"id": "ham70_fm", "name": "70cm Ham FM Relais", "freq_hz": 438_650_000, "start_hz": 438_600_000, "end_hz": 439_425_000, "step_hz": 12_500, "mod": "NFM", "enabled": False},
    {"id": "ham2m_fm", "name": "2m Ham FM Relais", "freq_hz": 145_600_000, "start_hz": 145_600_000, "end_hz": 145_800_000, "step_hz": 12_500, "mod": "NFM", "enabled": False},
    {"id": "pmr_fm", "name": "PMR446 Analog", "freq_hz": 446_006_250, "start_hz": 446_006_250, "end_hz": 446_193_750, "step_hz": 12_500, "mod": "NFM", "enabled": False},
    {"id": "air_am", "name": "Airband VHF (AM)", "freq_hz": 121_500_000, "start_hz": 118_000_000, "end_hz": 136_975_000, "step_hz": 25_000, "mod": "AM", "enabled": False},
    {"id": "marine", "name": "Marine VHF Ch16", "freq_hz": 156_800_000, "start_hz": 156_000_000, "end_hz": 157_425_000, "step_hz": 25_000, "mod": "NFM", "enabled": False},
    {"id": "freenet", "name": "Freenet 149 MHz", "freq_hz": 149_025_000, "start_hz": 149_025_000, "end_hz": 149_112_500, "step_hz": 12_500, "mod": "NFM", "enabled": False},
]

PAGER_PRESETS = [
    {"name": "DAPNET Germany (70cm)", "freq_hz": 439_987_500},
    {"name": "DAPNET Alt / Ham", "freq_hz": 439_975_000},
    {"name": "70cm POCSAG 466.075", "freq_hz": 466_075_000},
    {"name": "70cm POCSAG 466.206", "freq_hz": 466_206_250},
    {"name": "2m POCSAG 143.900", "freq_hz": 143_900_000},
]

APRS_PRESETS = [
    {"name": "Europe APRS (2m)", "freq_hz": 144_800_000},
    {"name": "ISS Digipeater", "freq_hz": 145_825_000},
    {"name": "70cm APRS EU", "freq_hz": 432_500_000},
    {"name": "US / NA APRS", "freq_hz": 144_390_000},
]

ISM433_PRESETS = [
    {"name": "EU ISM 433.920 MHz", "freq_hz": 433_920_000},
    {"name": "433.420 MHz (Blinds)", "freq_hz": 433_420_000},
    {"name": "434.420 MHz Sensors", "freq_hz": 434_420_000},
    {"name": "EU 868.300 MHz", "freq_hz": 868_300_000},
]

SONDE_PRESETS = [
    {"name": "Auto-Sweep 400-406 MHz", "freq_hz": 0},
    {"name": "RS41 Common 402.500", "freq_hz": 402_500_000},
    {"name": "RS41 Common 403.000", "freq_hz": 403_000_000},
    {"name": "RS41 Common 404.000", "freq_hz": 404_000_000},
    {"name": "RS41 Common 405.000", "freq_hz": 405_000_000},
]

DEFAULT_CHANNELS = [
    {"freq_hz": 433_450_000, "name": "70cm DMR Simp 1", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 433_475_000, "name": "70cm DMR Simp 2", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 438_425_000, "name": "70cm DMR Local", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 446_106_250, "name": "PMR446 DMR Ch1", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
    {"freq_hz": 446_118_750, "name": "PMR446 DMR Ch2", "cc": "", "last_tg": "", "hits": 0, "source": "PRESET"},
]


def fmt_mhz(freq_hz: int) -> str:
    return f"{freq_hz / 1_000_000.0:.4f}"


def fmt_mhz_short(freq_hz: int) -> str:
    return f"{freq_hz / 1_000_000.0:.3f}"


# ==============================================================================
# AUDIO CONTROL (PipeWire / PulseAudio + Seamless Live WAV Recording & Playback)
# ==============================================================================
class AudioControl:
    def __init__(self) -> None:
        self.volume_pct = 80
        self.muted = False
        self._play_proc: subprocess.Popen | None = None
        self.playing_file = ""
        self._rec_proc: subprocess.Popen | None = None
        self.rec_file = ""
        self.rec_started_at = 0.0
        self._sync_from_system()

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return env

    def _run_pactl(self, args: list[str]) -> str:
        try:
            out = subprocess.check_output(["pactl"] + args, env=self._env(), stderr=subprocess.DEVNULL, timeout=1.0)
            return out.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _sync_from_system(self) -> None:
        out = self._run_pactl(["get-sink-volume", "@DEFAULT_SINK@"])
        for tok in out.split():
            if tok.endswith("%") and tok[:-1].isdigit():
                self.volume_pct = max(0, min(100, int(tok[:-1])))
                break
        m = self._run_pactl(["get-sink-mute", "@DEFAULT_SINK@"])
        if "yes" in m.lower():
            self.muted = True

    def set_volume(self, pct: int) -> None:
        self.volume_pct = max(0, min(100, int(pct)))
        if self.muted and self.volume_pct > 0:
            self.muted = False
            threading.Thread(target=self._run_pactl, args=(["set-sink-mute", "@DEFAULT_SINK@", "0"],), daemon=True).start()
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

    def start_recording(self, tag: str) -> str:
        """
        Seamless live WAV recording directly from PipeWire output monitor via pw-record.
        Never restarts dsd-fme or rtl_fm — zero audio drop, zero return to scan screen!
        """
        self.stop_recording()
        os.makedirs(REC_DIR, exist_ok=True)
        safe_tag = "".join(c if c.isalnum() or c in "._-" else "_" for c in tag)[:36]
        ts = time.strftime("%Y%m%d_%H%M%S")
        fname = f"{safe_tag}_{ts}.wav"
        fpath = os.path.join(REC_DIR, fname)
        sink = self._run_pactl(["get-default-sink"]).strip()
        cmd = ["pw-record", "--rate=16000", "--channels=1", "--format=s16"]
        if sink:
            cmd.extend(["--target", f"{sink}.monitor"])
        else:
            cmd.extend(["-P", "stream.capture.sink=true"])
        cmd.append(fpath)
        try:
            self._rec_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=self._env(),
            )
            self.rec_file = fname
            self.rec_started_at = time.time()
            return fname
        except Exception:
            self._rec_proc = None
            self.rec_file = ""
            return ""

    def stop_recording(self) -> str:
        fn = self.rec_file
        proc = self._rec_proc
        self._rec_proc = None
        self.rec_file = ""
        self.rec_started_at = 0.0
        if proc is not None:
            try:
                # SIGINT lets parec cleanly flush and finalize the WAV header
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=0.5)
                except Exception:
                    pass
        return fn

    def is_recording(self) -> bool:
        if self._rec_proc is not None and self._rec_proc.poll() is not None:
            self._rec_proc = None
            self.rec_file = ""
        return self._rec_proc is not None

    def play_wav(self, path: str) -> None:
        self.stop_playback()
        try:
            self._play_proc = subprocess.Popen(
                ["paplay", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._env(),
            )
            self.playing_file = os.path.basename(path)
        except Exception:
            self.playing_file = ""

    def stop_playback(self) -> None:
        if self._play_proc is not None:
            try:
                self._play_proc.terminate()
                self._play_proc.wait(timeout=0.5)
            except Exception:
                pass
            self._play_proc = None
        self.playing_file = ""

    def is_playing(self) -> bool:
        if self._play_proc is not None and self._play_proc.poll() is not None:
            self._play_proc = None
            self.playing_file = ""
        return self._play_proc is not None


# ==============================================================================
# UNIFIED ALL-MODE RTL-SDR ENGINE
# ==============================================================================
class ZeroWaveEngine:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.db = RadioDatabase(DB_FILE)

        self.sweeper = RtlSdrSweeper(device_index=0)
        self.dsd = DsdRunner()
        self.analog = AnalogRunner()
        self.pager = PagerRunner()
        self.aprs = AprsRunner()
        self.ism = Ism433Runner()
        self.sonde = SondeRunner()

        self.dsd.on_call_event = self._on_event
        self.pager.on_message = self._on_event
        self.aprs.on_packet = self._on_event
        self.ism.on_sensor = self._on_event
        self.sonde.on_sonde = self._on_event

        self.config = {
            "app_mode": "VOICE",          # VOICE, ANALOG, PAGER, APRS, ISM433, SONDE
            "voice_scan": "AUTO",         # AUTO, LIST, TUNER
            "decoder_flag": "-fs",        # -fs, -fa, -fy, -fd, -fz, -fm, -fi, -fn, -f1, -ft
            "recording": False,           # Global WAV recording toggle
            "gain_db": 0,                 # 0 = Auto Hardware AGC
            "bw_khz": 12,
            "ppm": 0,
            "threshold_db": 11.0,
            "verify_sec": 2.2,
            "hang_sec": 4.0,
            "cooldown_sec": 22.0,
            "auto_save_found": True,
            "manual_freq_hz": 438_425_000,
            "analog_freq_hz": 100_000_000,
            "analog_mod": "WFM",          # NFM, AM, WFM
            "analog_squelch": 0,
            "analog_scan": True,
            "analog_bands": [dict(b) for b in DEFAULT_ANALOG_BANDS],
            "pager_freq_hz": 439_987_500,
            "aprs_freq_hz": 144_800_000,
            "ism_freq_hz": 433_920_000,
            "sonde_freq_hz": 403_000_000,
            "sonde_sweep": True,
            "bands": [dict(b) for b in DEFAULT_VOICE_BANDS],
        }
        self.channels: list[dict] = [dict(c) for c in DEFAULT_CHANNELS]
        self.blacklist: set[int] = set()
        self.event_logs: dict[str, collections.deque[dict]] = {
            m[0]: collections.deque(maxlen=200) for m in APP_MODES
        }
        self._ism_last_log: dict[str, float] = {}

        self._load_all()

        # Live status
        self.phase = "IDLE"
        self.hold = False
        self.paused = False
        self.active_freq_hz: int = int(self.config["manual_freq_hz"])
        self.active_label: str = ""
        self.active_snr_db: float = 0.0
        self.status_text: str = "Starting RTL-SDR..."
        self.error_text: str = ""

        # Live spectrum data
        self.sweep_freqs: np.ndarray | None = None
        self.sweep_psd: np.ndarray | None = None
        self.sweep_noise: float = -45.0
        self.sweep_peaks: list[PeakCandidate] = []
        self.cooldowns: dict[int, float] = {}
        self.skipped_count: int = 0
        self.found_count: int = 0

        self._stop_evt = threading.Event()
        self._interrupt_evt = threading.Event()
        self._tune_debounce_until = 0.0
        self._thread: threading.Thread | None = None

    # --- Persistence ----------------------------------------------------------
    def _load_all(self) -> None:
        # Migrate legacy ZeroDMR channels if ZeroWave config doesn't exist yet
        legacy_ch = os.path.expanduser("~/.config/zerodmr/channels.json")
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
            src_ch = CHANNELS_FILE if os.path.exists(CHANNELS_FILE) else legacy_ch
            if os.path.exists(src_ch):
                with open(src_ch, "r", encoding="utf-8") as f:
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
        # Load per-mode logs (migrating legacy unified event_log.json if needed)
        loaded_any = False
        for mode_id in self.event_logs:
            p = os.path.join(CONFIG_DIR, f"log_{mode_id.lower()}.json")
            try:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        logs = json.load(f)
                    if isinstance(logs, list):
                        for item in logs[-200:]:
                            if isinstance(item, dict):
                                self.event_logs[mode_id].append(item)
                        loaded_any = True
            except Exception:
                pass
        if not loaded_any:
            try:
                if os.path.exists(LOG_FILE):
                    with open(LOG_FILE, "r", encoding="utf-8") as f:
                        logs = json.load(f)
                    if isinstance(logs, list):
                        for item in logs[-250:]:
                            if isinstance(item, dict):
                                k = str(item.get("kind", "VOICE")).upper()
                                if k in self.event_logs:
                                    self.event_logs[k].append(item)
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

    def save_log(self, mode_id: str | None = None) -> None:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            targets = [mode_id] if (mode_id and mode_id in self.event_logs) else list(self.event_logs.keys())
            for mid in targets:
                p = os.path.join(CONFIG_DIR, f"log_{mid.lower()}.json")
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(list(self.event_logs[mid]), f)
        except Exception:
            pass

    def get_mode_log(self, mode_id: str | None = None) -> list[dict]:
        with self.lock:
            mid = mode_id or self.config["app_mode"]
            return list(self.event_logs.get(mid, ()))

    def clear_mode_log(self, mode_id: str | None = None) -> None:
        with self.lock:
            mid = mode_id or self.config["app_mode"]
            if mid in self.event_logs:
                self.event_logs[mid].clear()
        self.save_log(mid)

    def _stop_all_backends(self) -> None:
        was_active = any((
            self.dsd.get_state().running,
            self.analog.running,
            self.pager.running,
            self.aprs.running,
            self.ism.running,
            self.sonde.running,
            self.sweeper.is_open,
        ))
        self.dsd.stop()
        self.analog.stop()
        self.pager.stop()
        self.aprs.stop()
        self.ism.stop()
        self.sonde.stop()
        self.sweeper.close()
        # Brief USB settle pause prevents rapid open/close bus resets on dwc2
        if was_active:
            time.sleep(0.12)

    # --- Public controls ------------------------------------------------------
    def start(self) -> None:
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        self._interrupt_evt.set()
        self._stop_all_backends()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.db.close()
        self.save_config()
        self.save_channels()
        self.save_blacklist()
        self.save_log()

    def set_app_mode(self, app_mode: str) -> None:
        with self.lock:
            self.config["app_mode"] = app_mode
            self.hold = False
            self.paused = False
            self.error_text = ""
        self.save_config()
        self._interrupt_evt.set()

    def set_voice_scan(self, mode: str) -> None:
        with self.lock:
            self.config["app_mode"] = "VOICE"
            self.config["voice_scan"] = mode
            self.hold = False
            self.paused = False
        self.save_config()
        self._interrupt_evt.set()

    def toggle_recording(self) -> bool:
        with self.lock:
            self.config["recording"] = not self.config.get("recording", False)
            rec = bool(self.config["recording"])
        self.save_config()
        self._interrupt_evt.set()
        return rec

    def tune_freq(self, freq_hz: int, label: str = "") -> None:
        with self.lock:
            am = self.config["app_mode"]
            f = max(24_000_000, min(1_700_000_000, int(freq_hz)))
            self.active_freq_hz = f
            self.active_label = label or self._lookup_channel_name(f)
            self.paused = False
            if am == "VOICE":
                self.config["voice_scan"] = "TUNER"
                self.config["manual_freq_hz"] = f
            elif am == "ANALOG":
                self.config["analog_scan"] = False
                self.config["analog_freq_hz"] = f
            elif am == "PAGER":
                self.config["pager_freq_hz"] = f
            elif am == "APRS":
                self.config["aprs_freq_hz"] = f
            elif am == "ISM433":
                self.config["ism_freq_hz"] = f
            elif am == "SONDE":
                self.config["sonde_sweep"] = False
                self.config["sonde_freq_hz"] = f
        self.save_config()
        self._interrupt_evt.set()

    def step_freq(self, delta_hz: int) -> None:
        with self.lock:
            am = self.config["app_mode"]
            cur = self.active_freq_hz
            if am == "ANALOG":
                mod = self.config.get("analog_mod", "NFM")
                if abs(delta_hz) == 12_500:
                    if mod == "WFM":
                        delta_hz = 100_000 if delta_hz > 0 else -100_000
                    elif mod == "AM":
                        delta_hz = 25_000 if delta_hz > 0 else -25_000
                elif abs(delta_hz) == 100_000 and mod == "WFM":
                    delta_hz = 1_000_000 if delta_hz > 0 else -1_000_000
            self._tune_debounce_until = time.time() + 0.22
            self.tune_freq(cur + delta_hz)

    def cycle_analog_mod(self) -> str:
        with self.lock:
            cur = self.config.get("analog_mod", "NFM")
            nxt = ANALOG_MODS[(ANALOG_MODS.index(cur) + 1) % len(ANALOG_MODS)] if cur in ANALOG_MODS else "NFM"
            self.config["analog_mod"] = nxt
            # Hold in TUNER on current freq so AUTO band-sweep doesn't snap analog_mod back to the band's default
            if self.config.get("analog_scan", False):
                self.config["analog_scan"] = False
                if self.active_freq_hz > 0:
                    self.config["analog_freq_hz"] = int(self.active_freq_hz)
        self.save_config()
        self._interrupt_evt.set()
        return nxt

    def toggle_hold(self) -> bool:
        with self.lock:
            am = self.config["app_mode"]
            if am == "VOICE" and self.config["voice_scan"] != "TUNER":
                self.hold = not self.hold
                return self.hold
            if am == "ANALOG" and self.config.get("analog_scan"):
                self.hold = not self.hold
                return self.hold
            self.paused = not self.paused
            self._interrupt_evt.set()
            return self.paused

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
        expired = [k for k, exp in self.cooldowns.items() if exp <= now]
        for k in expired:
            del self.cooldowns[k]
        for cf in self.cooldowns:
            if abs(cf - freq_hz) <= 6_250:
                return True
        return False

    def _on_event(self, ev: dict) -> None:
        kind = str(ev.get("kind", "VOICE")).upper()
        if kind not in self.event_logs:
            kind = "VOICE"
        with self.lock:
            # Rate-limit ISM433 sensor log spam (max 1 log entry per 30s per sensor)
            if kind == "ISM433":
                skey = f"{ev.get('model', '')}|{ev.get('id', '')}"
                now = time.time()
                if now - self._ism_last_log.get(skey, 0.0) < 30.0:
                    return
                self._ism_last_log[skey] = now
            # Enrich VOICE event with RadioID & Talkgroup DB
            if kind == "VOICE":
                src = ev.get("src", "")
                if src.isdigit():
                    info = self.db.lookup_id(src)
                    if info:
                        ev["callsign"] = info["callsign"]
                        ev["op_name"] = info["first_name"]
                        ev["country"] = info["country"]
                tg = ev.get("tg", "")
                if tg.isdigit():
                    tgn = self.db.lookup_tg(tg)
                    if tgn:
                        ev["tg_name"] = tgn
                self.found_count += 1
                if self.config.get("auto_save_found", True):
                    self.save_favorite(
                        freq_hz=ev["freq_hz"],
                        cc=ev.get("cc", ""),
                        tg=tg,
                        source="AUTO",
                    )
            self.event_logs[kind].appendleft(ev)
        self.save_log(kind)

    # --- Background Worker Loop -----------------------------------------------
    def _worker_loop(self) -> None:
        while not self._stop_evt.is_set():
            self._interrupt_evt.clear()
            # Debounce rapid frequency stepping so we don't hammer the USB dongle
            with self.lock:
                deb = self._tune_debounce_until - time.time()
            if deb > 0:
                self._interrupt_evt.wait(min(0.25, deb))
                continue
            with self.lock:
                app_mode = self.config["app_mode"]
                paused = self.paused

            if paused:
                self._stop_all_backends()
                with self.lock:
                    self.phase = "IDLE"
                    self.status_text = "Paused (SPACE to resume)"
                self._interrupt_evt.wait(0.25)
                continue

            try:
                if app_mode == "VOICE":
                    self._run_voice_mode()
                elif app_mode == "ANALOG":
                    self._run_analog_mode()
                elif app_mode == "PAGER":
                    self._run_pager_mode()
                elif app_mode == "APRS":
                    self._run_aprs_mode()
                elif app_mode == "ISM433":
                    self._run_ism_mode()
                elif app_mode == "SONDE":
                    self._run_sonde_mode()
                else:
                    self._interrupt_evt.wait(0.3)
            except RtlSdrError as e:
                self._stop_all_backends()
                with self.lock:
                    self.phase = "ERROR"
                    self.error_text = str(e)[:46]
                    self.status_text = f"SDR Error: {self.error_text}"
                self._interrupt_evt.wait(2.0)
            except Exception as e:
                self._stop_all_backends()
                with self.lock:
                    self.phase = "ERROR"
                    self.error_text = str(e)[:46]
                    self.status_text = f"Err: {self.error_text}"
                self._interrupt_evt.wait(1.5)

    # --- 1) VOICE MODE (AUTO / LIST / TUNER) ----------------------------------
    def _run_voice_mode(self) -> None:
        with self.lock:
            vmode = self.config["voice_scan"]
        if vmode == "TUNER":
            self._run_voice_tuner()
        elif vmode == "LIST":
            self._run_voice_list()
        else:
            self._run_voice_auto()

    def _run_voice_tuner(self) -> None:
        self._stop_all_backends()
        with self.lock:
            cfg = dict(self.config)
            freq = int(cfg["manual_freq_hz"])
            self.active_freq_hz = freq
            self.active_label = self._lookup_channel_name(freq) or "Digital Tuner"
            self.phase = "TUNER"
            self.status_text = f"Listening {fmt_mhz(freq)} MHz..."
            self.error_text = ""

        self.dsd.start(
            freq_hz=freq,
            mode_flag=str(cfg["decoder_flag"]),
            gain_db=int(cfg["gain_db"]),
            ppm=int(cfg["ppm"]),
            bw_khz=int(cfg.get("bw_khz", 12)),
            squelch=0,
            vol=2,
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
                    self.status_text = f"{st.protocol or 'DV'} LOCK CC:{st.color_code or '-'}"
                else:
                    self.phase = "TUNER"
                    self.status_text = f"Listening {fmt_mhz(freq)} MHz (no sync)"
            if not st.running:
                time.sleep(0.5)
                break
            self._interrupt_evt.wait(0.15)
        self.dsd.stop()

    def _run_voice_list(self) -> None:
        with self.lock:
            ch_list = [int(c["freq_hz"]) for c in self.channels if not self._is_ignored(int(c["freq_hz"]))]
            cfg = dict(self.config)

        if not ch_list:
            with self.lock:
                self.phase = "IDLE"
                self.status_text = "Channel list empty! Add in LIST tab."
            self._interrupt_evt.wait(0.6)
            return

        self._stop_all_backends()
        gain = None if int(cfg["gain_db"]) == 0 else float(cfg["gain_db"])
        self.sweeper.open(gain_db=gain, ppm=int(cfg["ppm"]))

        with self.lock:
            self.phase = "SWEEP"
            self.active_label = f"LIST ({len(ch_list)} ch)"
            self.status_text = f"Checking {len(ch_list)} saved channels..."
            self.error_text = ""

        def _on_win(center: int, freqs: np.ndarray, psd: np.ndarray, nf: float) -> None:
            with self.lock:
                self.sweep_freqs = freqs
                self.sweep_psd = psd
                self.sweep_noise = nf
                self.active_freq_hz = center

        hits = self.sweeper.measure_channels_fast(ch_list, threshold_db=float(cfg["threshold_db"]), on_window=_on_win)
        with self.lock:
            self.sweep_peaks = hits

        if self._interrupt_evt.is_set() or self._stop_evt.is_set():
            return
        if not hits:
            self._interrupt_evt.wait(0.12)
            return

        self.sweeper.close()
        for peak in hits[:3]:
            if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                break
            label = self._lookup_channel_name(peak.freq_hz) or "Saved List"
            if self._verify_and_monitor_voice(peak, label):
                break

    def _run_voice_auto(self) -> None:
        with self.lock:
            enabled_bands = [dict(b) for b in self.config["bands"] if b.get("enabled")]
            cfg = dict(self.config)

        if not enabled_bands:
            with self.lock:
                self.phase = "IDLE"
                self.status_text = "No band enabled! Open 2:BAND."
            self._interrupt_evt.wait(0.6)
            return

        self._stop_all_backends()
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
                    self.sweep_freqs = freqs
                    self.sweep_psd = psd_db
                    self.sweep_noise = noise_floor
                    self.sweep_peaks = peaks

                if peaks:
                    self.sweeper.close()
                    for peak in peaks[:3]:
                        if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                            return
                        self._verify_and_monitor_voice(peak, bname)
                    if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                        return
                    self.sweeper.open(gain_db=gain, ppm=int(cfg["ppm"]))

    def _verify_and_monitor_voice(self, peak: PeakCandidate, label: str) -> bool:
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
                        self.status_text = f"{st.protocol or 'DV'} LOCK {fmt_mhz(peak.freq_hz)}"
                if not locked:
                    if (now - t_start) >= verify_sec:
                        with self.lock:
                            self.cooldowns[peak.freq_hz] = now + cooldown_sec
                            self.skipped_count += 1
                            self.status_text = f"No digital sync {fmt_mhz(peak.freq_hz)}"
                        break
                else:
                    with self.lock:
                        is_hold = self.hold
                    if not is_hold and (now - last_activity) >= hang_sec:
                        with self.lock:
                            self.status_text = f"Call ended {fmt_mhz(peak.freq_hz)}"
                        break
                if not st.running:
                    break
                self._interrupt_evt.wait(0.1)
        finally:
            self.dsd.stop()
        return locked

    # --- 2) ANALOG MODE (Unified AUTO Spectrum Sweep <-> TUNER like VOICE!) ---
    def _run_analog_mode(self) -> None:
        self._stop_all_backends()
        with self.lock:
            cfg = dict(self.config)
            do_scan = bool(cfg.get("analog_scan", True))
            mod = str(cfg.get("analog_mod", "WFM"))
            abands = [dict(b) for b in cfg.get("analog_bands", DEFAULT_ANALOG_BANDS) if b.get("enabled")]

        if do_scan:
            if not abands:
                with self.lock:
                    self.phase = "IDLE"
                    self.status_text = "No analog band ON! Open 2:BAND."
                self._interrupt_evt.wait(0.6)
                return

            gain = None if int(cfg["gain_db"]) == 0 else float(cfg["gain_db"])
            self.sweeper.open(gain_db=gain, ppm=int(cfg["ppm"]))

            for band in abands:
                if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                    return
                bmod = str(band.get("mod", "NFM"))
                bname = str(band["name"])
                start_hz = int(band["start_hz"])
                end_hz = int(band["end_hz"])
                step_hz = int(band.get("step_hz", 12_500))
                centers = plan_band_centers(start_hz, end_hz)

                for center in centers:
                    if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                        return
                    with self.lock:
                        self.phase = "SWEEP"
                        self.config["analog_mod"] = bmod
                        self.active_freq_hz = center
                        self.active_label = bname
                        self.status_text = f"Sweep {bname}: {fmt_mhz_short(center)} MHz"
                        self.error_text = ""

                    freqs, psd, nf = self.sweeper.capture_psd(center, burst_ms=55)
                    max_bw = 260_000 if bmod == "WFM" else 34_000
                    min_bw = 25_000 if bmod == "WFM" else 2_500
                    peaks = self.sweeper.find_peaks_in_window(
                        freqs, psd, nf, start_hz, end_hz,
                        step_hz=step_hz, threshold_db=float(cfg["threshold_db"]),
                        min_bw_hz=min_bw, max_bw_hz=max_bw, ignore_fn=self._is_ignored,
                    )
                    with self.lock:
                        self.sweep_freqs = freqs
                        self.sweep_psd = psd
                        self.sweep_noise = nf
                        self.sweep_peaks = peaks

                    if peaks:
                        pk = peaks[0]
                        self.sweeper.close()
                        time.sleep(0.12)
                        with self.lock:
                            self.phase = "LOCKED"
                            self.active_freq_hz = pk.freq_hz
                            self.active_label = bname
                            self.active_snr_db = pk.snr_db
                            self.status_text = f"{bmod} LOCK +{pk.snr_db:.0f}dB (B:Next SPC:Hold)"
                        self._on_event({
                            "kind": "ANALOG",
                            "ts": time.time(),
                            "freq_hz": pk.freq_hz,
                            "mod": bmod,
                            "snr": round(pk.snr_db, 1),
                            "band": bname,
                        })
                        self.analog.start(
                            freq_hz=pk.freq_hz, mod=bmod,
                            gain_db=int(cfg["gain_db"]), ppm=int(cfg["ppm"]),
                            squelch=int(cfg.get("analog_squelch", 0)),
                        )
                        t0 = time.time()
                        while not self._stop_evt.is_set() and not self._interrupt_evt.is_set():
                            with self.lock:
                                is_hold = self.hold
                            if not is_hold and (time.time() - t0) >= 12.0:
                                with self.lock:
                                    self.cooldowns[pk.freq_hz] = time.time() + float(cfg["cooldown_sec"])
                                break
                            self._interrupt_evt.wait(0.15)
                        self.analog.stop()
                        time.sleep(0.12)
                        if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                            return
                        self.sweeper.open(gain_db=gain, ppm=int(cfg["ppm"]))
            return

        # Direct Analog Tuner
        freq = int(cfg.get("analog_freq_hz", 100_000_000))
        with self.lock:
            self.active_freq_hz = freq
            self.active_label = f"Analog {mod}"
            self.phase = "TUNER"
            self.status_text = f"{mod} {fmt_mhz(freq)} MHz | SQ:{cfg.get('analog_squelch', 0)}"
        self.analog.start(
            freq_hz=freq, mod=mod,
            gain_db=int(cfg["gain_db"]), ppm=int(cfg["ppm"]),
            squelch=int(cfg.get("analog_squelch", 0)),
        )
        while not self._stop_evt.is_set() and not self._interrupt_evt.is_set():
            self._interrupt_evt.wait(0.2)
        self.analog.stop()

    # --- 3) PAGER MODE (POCSAG / FLEX) ----------------------------------------
    def _run_pager_mode(self) -> None:
        self._stop_all_backends()
        with self.lock:
            cfg = dict(self.config)
            freq = int(cfg.get("pager_freq_hz", 439_987_500))
            self.active_freq_hz = freq
            self.active_label = "POCSAG / FLEX"
            self.phase = "LOCKED"
            self.status_text = f"Listening POCSAG on {fmt_mhz(freq)} MHz"
        self.pager.start(freq_hz=freq, gain_db=int(cfg["gain_db"]), ppm=int(cfg["ppm"]))
        while not self._stop_evt.is_set() and not self._interrupt_evt.is_set():
            self._interrupt_evt.wait(0.2)
        self.pager.stop()

    # --- 4) APRS MODE (AFSK1200) ----------------------------------------------
    def _run_aprs_mode(self) -> None:
        self._stop_all_backends()
        with self.lock:
            cfg = dict(self.config)
            freq = int(cfg.get("aprs_freq_hz", 144_800_000))
            self.active_freq_hz = freq
            self.active_label = "APRS AFSK1200"
            self.phase = "LOCKED"
            self.status_text = f"Listening APRS on {fmt_mhz(freq)} MHz"
        self.aprs.start(freq_hz=freq, gain_db=int(cfg["gain_db"]), ppm=int(cfg["ppm"]))
        while not self._stop_evt.is_set() and not self._interrupt_evt.is_set():
            self._interrupt_evt.wait(0.2)
        self.aprs.stop()

    # --- 5) ISM 433 MHz SENSOR MODE (rtl_433) ---------------------------------
    def _run_ism_mode(self) -> None:
        self._stop_all_backends()
        with self.lock:
            cfg = dict(self.config)
            freq = int(cfg.get("ism_freq_hz", 433_920_000))
            self.active_freq_hz = freq
            self.active_label = "rtl_433 Sensors"
            self.phase = "LOCKED"
            self.status_text = f"Monitoring ISM {fmt_mhz(freq)} MHz"
        self.ism.start(freq_hz=freq, gain_db=int(cfg["gain_db"]), ppm=int(cfg["ppm"]))
        while not self._stop_evt.is_set() and not self._interrupt_evt.is_set():
            self._interrupt_evt.wait(0.2)
        self.ism.stop()

    # --- 6) RADIOSONDE MODE (400-406 MHz Sweep + RS41 Telemetry) --------------
    def _run_sonde_mode(self) -> None:
        self._stop_all_backends()
        with self.lock:
            cfg = dict(self.config)
            do_sweep = bool(cfg.get("sonde_sweep", True))

        if do_sweep:
            gain = None if int(cfg["gain_db"]) == 0 else float(cfg["gain_db"])
            self.sweeper.open(gain_db=gain, ppm=int(cfg["ppm"]))
            centers = plan_band_centers(400_000_000, 406_000_000)
            for center in centers:
                if self._interrupt_evt.is_set() or self._stop_evt.is_set():
                    return
                with self.lock:
                    self.phase = "SWEEP"
                    self.active_freq_hz = center
                    self.active_label = "Sonde 400-406"
                    self.status_text = f"Sweep Sonde {fmt_mhz_short(center)} MHz"
                freqs, psd, nf = self.sweeper.capture_psd(center, burst_ms=55)
                peaks = self.sweeper.find_peaks_in_window(
                    freqs, psd, nf, 400_000_000, 406_000_000,
                    step_hz=10_000, threshold_db=max(9.0, float(cfg["threshold_db"]) - 1.0),
                    min_bw_hz=3_000, max_bw_hz=22_000, ignore_fn=self._is_ignored,
                )
                with self.lock:
                    self.sweep_freqs = freqs
                    self.sweep_psd = psd
                    self.sweep_noise = nf
                    self.sweep_peaks = peaks
                if peaks:
                    pk = peaks[0]
                    self.sweeper.close()
                    with self.lock:
                        self.phase = "CHECK"
                        self.active_freq_hz = pk.freq_hz
                        self.status_text = f"RS41 Check {fmt_mhz(pk.freq_hz)} (+{pk.snr_db:.0f}dB)"
                    self.sonde.start(freq_hz=pk.freq_hz, gain_db=int(cfg["gain_db"]), ppm=int(cfg["ppm"]))
                    t0 = time.time()
                    got_frame = False
                    while not self._stop_evt.is_set() and not self._interrupt_evt.is_set():
                        lat, _ = self.sonde.get_latest()
                        if lat:
                            got_frame = True
                            with self.lock:
                                self.phase = "LOCKED"
                                self.status_text = f"RS41 {lat['sonde_id']} ALT {lat['alt_m']}m"
                        if not got_frame and (time.time() - t0) >= 4.0:
                            with self.lock:
                                self.cooldowns[pk.freq_hz] = time.time() + 30.0
                            break
                        self._interrupt_evt.wait(0.2)
                    self.sonde.stop()
                    return
            return

        # Manual Sonde Decode
        freq = int(cfg.get("sonde_freq_hz", 403_000_000))
        with self.lock:
            self.active_freq_hz = freq
            self.active_label = "RS41 Decoder"
            self.phase = "TUNER"
            self.status_text = f"Decoding RS41 on {fmt_mhz(freq)} MHz..."
        self.sonde.start(freq_hz=freq, gain_db=int(cfg["gain_db"]), ppm=int(cfg["ppm"]))
        while not self._stop_evt.is_set() and not self._interrupt_evt.is_set():
            lat, _ = self.sonde.get_latest()
            if lat:
                with self.lock:
                    self.phase = "LOCKED"
                    self.status_text = f"RS41 {lat['sonde_id']} ALT {lat['alt_m']}m"
            self._interrupt_evt.wait(0.25)
        self.sonde.stop()


# ==============================================================================
# CARDPUTER ZERO 320x170 FRAMEBUFFER UI
# ==============================================================================
class ZeroWaveApp:
    TABS = ["LIVE", "BAND", "LIST", "LOG", "CFG"]

    def __init__(self, preview_out: str | None = None, preview_mode: str | None = None) -> None:
        self.preview_out = preview_out
        self.preview_mode = preview_mode

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
        self.f_lg = _f(FONT_MONO_BOLD, 15, True)
        self.f_xl = _f(FONT_MONO_BOLD, 18, True)
        self.f_mono = _f(FONT_MONO, 9)
        self.f_mono_b = _f(FONT_MONO_BOLD, 10, True)

        self.running = True
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))

        self.audio = AudioControl()
        self.engine = ZeroWaveEngine()

        self.tab_idx = 0
        self.band_sel = 0
        self.list_sel = 0
        self.log_sel = 0
        self.log_view = "EVENTS"  # EVENTS | RECS
        self.feed_scroll = 0
        self.cfg_sel = 0

        # Modals: None, "MODE_GRID", "FREQ_INPUT", "BAND_EDIT", "ADD_CHAN", "MSG_DETAIL"
        self.modal: str | None = None
        self.mode_grid_sel = 0
        self.input_buf = ""
        self.band_edit_field = 0
        self.band_edit_vals = ["", "", ""]
        self.detail_title = ""
        self.detail_body = ""

        self.toast_msg = ""
        self.toast_until = 0.0
        self.esc_down_at: float | None = None
        self.shift_held = False
        self.ctrl_held = False

    def show_toast(self, msg: str, dur: float = 1.9) -> None:
        self.toast_msg = msg
        self.toast_until = time.time() + dur

    def flush_fb(self) -> None:
        if self.fb_fd is not None:
            try:
                os.lseek(self.fb_fd, 0, os.SEEK_SET)
                os.write(self.fb_fd, bytes(self.screen.get_buffer()))
            except Exception:
                pass

    def _list_recordings(self) -> list[str]:
        if not os.path.isdir(REC_DIR):
            return []
        try:
            files = [f for f in os.listdir(REC_DIR) if f.lower().endswith(".wav")]
            files.sort(key=lambda fn: os.path.getmtime(os.path.join(REC_DIR, fn)), reverse=True)
            return files
        except Exception:
            return []

    # ==========================================================================
    # INPUT HANDLING (Cardputer Zero Full Keymap + FN+A/S/D + 3s ESC Hold)
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
        elif self.audio.is_playing():
            self.audio.stop_playback()
            self.show_toast("Playback stopped")
        elif self.tab_idx != 0:
            self.tab_idx = 0
        else:
            self.show_toast(f"Hold ESC {int(ESC_HOLD_SECONDS)}s to exit {APP_NAME}", 1.8)

    def on_key(self, code: int, ch: str = "") -> None:
        # Hardware Fn volume keys work everywhere (even inside modals)
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

        # Quick-Mode 2x3 Grid shortcut ('Q')
        if code == KEY_Q:
            cur = self.engine.config["app_mode"]
            ids = [m[0] for m in APP_MODES]
            self.mode_grid_sel = ids.index(cur) if cur in ids else 0
            self.modal = "MODE_GRID"
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
            self.band_sel = 0
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

        # Global mute / Sym volume / Recording
        if code == KEY_V:
            m = self.audio.toggle_mute()
            self.show_toast("Audio MUTED" if m else f"Audio ON ({self.audio.volume_pct}%)")
            return
        if ch in ("+", "=") or code in (KEY_EQUAL, 72, 73):
            self.audio.set_volume(self.audio.volume_pct + 5)
            self.show_toast(f"Volume: {self.audio.volume_pct}%")
            return
        if ch == "-" or code in (KEY_MINUS, 71):
            self.audio.set_volume(self.audio.volume_pct - 5)
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

    def _toggle_seamless_rec(self) -> None:
        if self.audio.is_recording():
            fn = self.audio.stop_recording()
            self.show_toast(f"Saved: {fn[:28]}" if fn else "Recording stopped")
            return
        with self.engine.lock:
            am = self.engine.config["app_mode"]
            freq = fmt_mhz(self.engine.active_freq_hz)
        if am == "VOICE":
            dsd = self.engine.dsd.get_state()
            proto = dsd.protocol or "DV"
            tg = dsd.slot1.tg or dsd.slot2.tg
            src = dsd.slot1.src or dsd.slot2.src
            op = self.engine.db.lookup_id(src) if src else None
            who = op["callsign"] if (op and op["callsign"]) else src
            parts = [proto, freq]
            if tg:
                parts.append(f"TG{tg}")
            if who:
                parts.append(who)
            tag = "_".join(parts)
        elif am == "ANALOG":
            tag = f"{self.engine.config.get('analog_mod', 'NFM')}_{freq}"
        else:
            tag = f"{am}_{freq}"
        fn = self.audio.start_recording(tag)
        self.show_toast("REC started (live, zero-drop)" if fn else "REC failed (parec)")

    def _key_live(self, code: int) -> None:
        am = self.engine.config["app_mode"]

        if code == KEY_R:
            self._toggle_seamless_rec()
            return

        if code == KEY_SPACE:
            res = self.engine.toggle_hold()
            if am == "VOICE" and self.engine.config["voice_scan"] != "TUNER":
                self.show_toast("HOLD ON" if res else "HOLD OFF (resuming)")
            elif am == "ANALOG" and self.engine.config.get("analog_scan"):
                self.show_toast("HOLD ON" if res else "HOLD OFF")
            else:
                self.show_toast("Paused" if res else "Resumed")
            return

        if code == KEY_F:
            self.modal = "FREQ_INPUT"
            self.input_buf = f"{self.engine.active_freq_hz / 1_000_000.0:.4f}"
            return

        if code == KEY_S and am in ("VOICE", "ANALOG"):
            st = self.engine.dsd.get_state()
            added = self.engine.save_favorite(cc=st.color_code, tg=st.slot1.tg or st.slot2.tg, source="FAV")
            self.show_toast("Saved to Favorites!" if added else "Updated Favorite!")
            return

        if code == KEY_B:
            f = self.engine.skip_current(permanent=False)
            self.show_toast(f"Skip {fmt_mhz(f)} (cooldown)")
            return

        if code == KEY_X:
            f = self.engine.skip_current(permanent=True)
            self.show_toast(f"Blacklisted {fmt_mhz(f)} MHz")
            return

        # Mode-specific live actions
        if am == "VOICE":
            if code == KEY_M:
                order = ["AUTO", "LIST", "TUNER"]
                cur = self.engine.config["voice_scan"]
                nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "AUTO"
                self.engine.set_voice_scan(nxt)
                self.show_toast(f"Voice Scan: {nxt}")
            elif code == KEY_LEFT:
                self.engine.step_freq(-12_500)
            elif code == KEY_RIGHT:
                self.engine.step_freq(+12_500)
            elif code == KEY_UP:
                self.engine.step_freq(+100_000)
            elif code == KEY_DOWN:
                self.engine.step_freq(-100_000)

        elif am == "ANALOG":
            if code == KEY_A:
                m = self.engine.cycle_analog_mod()
                self.show_toast(f"Modulation: {m}")
            elif code == KEY_M:
                self.engine.config["analog_scan"] = not self.engine.config.get("analog_scan", False)
                self.engine.notify_config_changed()
                self.show_toast("Analog Band Scan ON" if self.engine.config["analog_scan"] else "Analog Tuner")
            elif code == KEY_LEFT:
                self.engine.step_freq(-12_500)
            elif code == KEY_RIGHT:
                self.engine.step_freq(+12_500)
            elif code == KEY_UP:
                self.engine.step_freq(+100_000)
            elif code == KEY_DOWN:
                self.engine.step_freq(-100_000)

        elif am in ("PAGER", "APRS", "ISM433"):
            items = (
                self.engine.pager.get_messages() if am == "PAGER"
                else (self.engine.aprs.get_packets() if am == "APRS" else self.engine.ism.get_sensors())
            )
            if code == KEY_UP:
                self.feed_scroll = max(0, self.feed_scroll - 1)
            elif code == KEY_DOWN and items:
                self.feed_scroll = min(len(items) - 1, self.feed_scroll + 1)
            elif code == KEY_ENTER and items and am in ("PAGER", "APRS"):
                idx = max(0, min(len(items) - 1, self.feed_scroll))
                it = items[idx]
                self.detail_title = f"{it.get('proto', 'APRS')} {it.get('ric', it.get('src', ''))}"
                self.detail_body = str(it.get("text", ""))
                self.modal = "MSG_DETAIL"

        elif am == "SONDE":
            if code == KEY_M:
                self.engine.config["sonde_sweep"] = not self.engine.config.get("sonde_sweep", True)
                self.engine.notify_config_changed()
                self.show_toast("Sonde 400-406 Sweep" if self.engine.config["sonde_sweep"] else "Manual RS41 Tuner")
            elif code == KEY_LEFT:
                self.engine.step_freq(-10_000)
            elif code == KEY_RIGHT:
                self.engine.step_freq(+10_000)
            elif code == KEY_UP:
                self.engine.step_freq(+100_000)
            elif code == KEY_DOWN:
                self.engine.step_freq(-100_000)

    def _key_bands(self, code: int) -> None:
        am = self.engine.config["app_mode"]
        if am == "VOICE":
            bands = self.engine.config["bands"]
            if code == KEY_UP:
                self.band_sel = (self.band_sel - 1) % len(bands)
            elif code == KEY_DOWN:
                self.band_sel = (self.band_sel + 1) % len(bands)
            elif code in (KEY_SPACE, KEY_ENTER):
                bands[self.band_sel]["enabled"] = not bands[self.band_sel].get("enabled", False)
                self.engine.notify_config_changed()
                self.show_toast(f"{bands[self.band_sel]['name']}: {'ON' if bands[self.band_sel]['enabled'] else 'OFF'}")
            elif code == KEY_A:
                self.engine.set_voice_scan("AUTO")
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
            return

        if am == "ANALOG":
            bands = self.engine.config.get("analog_bands", DEFAULT_ANALOG_BANDS)
            self.band_sel = max(0, min(len(bands) - 1, self.band_sel))
            if code == KEY_UP:
                self.band_sel = (self.band_sel - 1) % len(bands)
            elif code == KEY_DOWN:
                self.band_sel = (self.band_sel + 1) % len(bands)
            elif code in (KEY_SPACE, KEY_ENTER):
                bands[self.band_sel]["enabled"] = not bands[self.band_sel].get("enabled", False)
                self.engine.config["analog_bands"] = bands
                self.engine.notify_config_changed()
                self.show_toast(f"{bands[self.band_sel]['name'][:18]}: {'ON' if bands[self.band_sel]['enabled'] else 'OFF'}")
            elif code == KEY_A:
                self.engine.config["analog_scan"] = True
                self.engine.notify_config_changed()
                self.tab_idx = 0
                self.show_toast("Started Analog AUTO Scan")
            elif code == KEY_T:
                b = bands[self.band_sel]
                self.engine.config["analog_mod"] = b["mod"]
                self.engine.tune_freq(b["freq_hz"], b["name"])
                self.tab_idx = 0
                self.show_toast(f"Tuned {b['name'][:20]}")
            return

        # Presets for PAGER / APRS / ISM433 / SONDE
        presets = self._current_mode_presets()
        if not presets:
            return
        self.band_sel = max(0, min(len(presets) - 1, self.band_sel))
        if code == KEY_UP:
            self.band_sel = (self.band_sel - 1) % len(presets)
        elif code == KEY_DOWN:
            self.band_sel = (self.band_sel + 1) % len(presets)
        elif code in (KEY_ENTER, KEY_SPACE):
            p = presets[self.band_sel]
            if am == "SONDE" and p["freq_hz"] == 0:
                self.engine.config["sonde_sweep"] = True
                self.engine.notify_config_changed()
            else:
                self.engine.tune_freq(p["freq_hz"], p["name"])
            self.tab_idx = 0
            self.show_toast(f"Selected: {p['name'][:22]}")

    def _current_mode_presets(self) -> list[dict]:
        am = self.engine.config["app_mode"]
        if am == "PAGER":
            return PAGER_PRESETS
        if am == "APRS":
            return APRS_PRESETS
        if am == "ISM433":
            return ISM433_PRESETS
        if am == "SONDE":
            return SONDE_PRESETS
        return []

    def _key_list(self, code: int) -> None:
        chs = self.engine.channels
        if code == KEY_N:
            self.modal = "ADD_CHAN"
            self.input_buf = f"{self.engine.active_freq_hz / 1e6:.4f}"
            return
        if code == KEY_S:
            self.engine.set_voice_scan("LIST")
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
            self.engine.set_app_mode("VOICE")
            self.engine.tune_freq(int(ch["freq_hz"]), ch.get("name", ""))
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
        if code in (KEY_LEFT, KEY_RIGHT, KEY_L):
            self.log_view = "RECS" if self.log_view == "EVENTS" else "EVENTS"
            self.log_sel = 0
            return

        if self.log_view == "RECS":
            recs = self._list_recordings()
            if code == KEY_SPACE and self.audio.is_playing():
                self.audio.stop_playback()
                self.show_toast("Playback stopped")
                return
            if not recs:
                return
            self.log_sel = max(0, min(len(recs) - 1, self.log_sel))
            if code == KEY_UP:
                self.log_sel = max(0, self.log_sel - 1)
            elif code == KEY_DOWN:
                self.log_sel = min(len(recs) - 1, self.log_sel + 1)
            elif code == KEY_ENTER:
                fn = recs[self.log_sel]
                self.audio.play_wav(os.path.join(REC_DIR, fn))
                self.show_toast(f"Playing {fn[:22]}")
            elif code == KEY_D:
                fn = recs[self.log_sel]
                try:
                    os.remove(os.path.join(REC_DIR, fn))
                    self.show_toast("Deleted recording")
                except Exception:
                    pass
            return

        am = self.engine.config["app_mode"]
        logs = self.engine.get_mode_log(am)
        if code == KEY_C:
            self.engine.clear_mode_log(am)
            self.log_sel = 0
            self.show_toast(f"{am} log cleared")
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
            kind = ev.get("kind", am)
            if kind in ("PAGER", "APRS"):
                self.detail_title = f"{kind} {ev.get('ric', ev.get('src', ''))}"
                self.detail_body = str(ev.get("text", ""))
                self.modal = "MSG_DETAIL"
            elif kind == "ISM433":
                self.detail_title = f"{ev.get('model', 'Sensor')} #{ev.get('id', '')}"
                self.detail_body = f"{fmt_mhz(int(ev.get('freq_hz', 433920000)))} MHz\n{ev.get('summary', '')}"
                self.modal = "MSG_DETAIL"
            elif kind == "ANALOG":
                self.engine.config["analog_mod"] = ev.get("mod", "NFM")
                self.engine.set_app_mode("ANALOG")
                self.engine.tune_freq(int(ev.get("freq_hz", 100_000_000)), ev.get("band", ""))
                self.tab_idx = 0
            elif kind == "SONDE":
                self.engine.set_app_mode("SONDE")
                self.engine.tune_freq(int(ev.get("freq_hz", 403_000_000)), ev.get("sonde_id", "RS41"))
                self.tab_idx = 0
            else:
                self.engine.set_app_mode("VOICE")
                self.engine.tune_freq(int(ev.get("freq_hz", 438_425_000)), ev.get("callsign", ""))
                self.tab_idx = 0

    def _key_cfg(self, code: int) -> None:
        n_items = 12
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
        if self.cfg_sel == 0:  # Callsign + TG DB Downloader
            if code in (KEY_ENTER, KEY_SPACE):
                ok = self.engine.db.start_download(on_done=lambda _ok, msg: self.show_toast(msg, 3.0))
                self.show_toast("Downloading RadioID + TG DB..." if ok else "Download already running")
            return
        elif self.cfg_sel == 1:  # Digital Voice Decoder
            flags = [m[0] for m in DECODER_MODES]
            idx = flags.index(cfg["decoder_flag"]) if cfg["decoder_flag"] in flags else 0
            cfg["decoder_flag"] = flags[(idx + delta) % len(flags)]
        elif self.cfg_sel == 2:  # Recording
            self._toggle_seamless_rec()
            return
        elif self.cfg_sel == 3:  # Gain
            g = int(cfg["gain_db"])
            idx = GAIN_OPTIONS.index(g) if g in GAIN_OPTIONS else 0
            cfg["gain_db"] = GAIN_OPTIONS[(idx + delta) % len(GAIN_OPTIONS)]
        elif self.cfg_sel == 4:  # BW
            bw = int(cfg.get("bw_khz", 12))
            idx = BW_OPTIONS.index(bw) if bw in BW_OPTIONS else 1
            cfg["bw_khz"] = BW_OPTIONS[(idx + delta) % len(BW_OPTIONS)]
        elif self.cfg_sel == 5:  # Analog Squelch
            cfg["analog_squelch"] = max(0, min(200, int(cfg.get("analog_squelch", 28)) + delta * 5))
        elif self.cfg_sel == 6:  # PPM
            cfg["ppm"] = max(-80, min(80, int(cfg["ppm"]) + delta))
        elif self.cfg_sel == 7:  # Peak Threshold
            cfg["threshold_db"] = round(max(6.0, min(25.0, float(cfg["threshold_db"]) + delta * 1.0)), 1)
        elif self.cfg_sel == 8:  # Verify Time
            cfg["verify_sec"] = round(max(1.0, min(5.0, float(cfg["verify_sec"]) + delta * 0.2)), 1)
        elif self.cfg_sel == 9:  # Hang Time
            cfg["hang_sec"] = round(max(1.5, min(12.0, float(cfg["hang_sec"]) + delta * 0.5)), 1)
        elif self.cfg_sel == 10:  # Auto-Save
            cfg["auto_save_found"] = not cfg.get("auto_save_found", True)
        elif self.cfg_sel == 11:  # Clear Blacklist
            if code in (KEY_ENTER, KEY_SPACE):
                self.engine.blacklist.clear()
                self.engine.cooldowns.clear()
                self.engine.save_blacklist()
                self.show_toast("Blacklist & skips cleared!")
            return
        self.engine.notify_config_changed()

    def _handle_modal_key(self, code: int, ch: str = "") -> None:
        if self.modal == "MODE_GRID":
            if code in (KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6):
                idx = code - KEY_1
                self._select_app_mode(idx)
            elif code == KEY_LEFT:
                self.mode_grid_sel = (self.mode_grid_sel - 1) % len(APP_MODES)
            elif code == KEY_RIGHT:
                self.mode_grid_sel = (self.mode_grid_sel + 1) % len(APP_MODES)
            elif code in (KEY_UP, KEY_DOWN):
                self.mode_grid_sel = (self.mode_grid_sel + 3) % len(APP_MODES)
            elif code in (KEY_ENTER, KEY_SPACE):
                self._select_app_mode(self.mode_grid_sel)
            elif code == KEY_Q:
                self.modal = None
            return

        if self.modal == "MSG_DETAIL":
            if code in (KEY_ENTER, KEY_SPACE):
                self.modal = None
            return

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
                            self.engine.tune_freq(hz)
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

    def _select_app_mode(self, idx: int) -> None:
        mode_id, title, _, _ = APP_MODES[idx % len(APP_MODES)]
        self.engine.set_app_mode(mode_id)
        self.feed_scroll = 0
        self.band_sel = 0
        self.log_sel = 0
        self.tab_idx = 0
        self.modal = None
        self.show_toast(f"Mode: {title}")

    # ==========================================================================
    # RENDERING
    # ==========================================================================
    def _pill(self, x: int, y: int, text: str, fg, bg, font=None) -> int:
        f = font or self.f_xxs
        surf = f.render(text, True, fg)
        pw = surf.get_width() + 7
        ph = surf.get_height() + 4
        pygame.draw.rect(self.screen, bg, (x, y, pw, ph), border_radius=3)
        self.screen.blit(surf, (x + 4, y + 2))
        return pw

    def draw_header(self) -> None:
        pygame.draw.rect(self.screen, SURFACE, (0, 0, W, 20))
        pygame.draw.line(self.screen, BORDER, (0, 19), (W, 19))

        with self.engine.lock:
            am = self.engine.config["app_mode"]
            phase = self.engine.phase
            hold = self.engine.hold
            rec = bool(self.engine.config.get("recording", False))

        # Active mode color badge on far left (press Q!)
        m_col = dict((m[0], m[3]) for m in APP_MODES).get(am, MINT)
        short_m = {"VOICE": "DV", "ANALOG": "AN", "PAGER": "PG", "APRS": "AP", "ISM433": "433", "SONDE": "RS"}.get(am, am[:2])
        mw = self._pill(3, 3, f"Q:{short_m}", BG, m_col)

        # Tabs 1..5
        tx = 3 + mw + 4
        for idx, name in enumerate(self.TABS):
            active = idx == self.tab_idx
            col = MINT if active else SUBTEXT
            lbl = self.f_xs_b.render(f"{idx+1}:{name}", True, col)
            if active:
                pygame.draw.rect(self.screen, MINT_DARK, (tx - 2, 2, lbl.get_width() + 5, 15), border_radius=3)
                pygame.draw.line(self.screen, MINT, (tx - 1, 18), (tx + lbl.get_width() + 2, 18), 2)
            self.screen.blit(lbl, (tx + 1, 4))
            tx += lbl.get_width() + 6

        # Right status indicators (REC + Mute + Phase)
        rx = W - 4
        if phase == "LOCKED":
            p_txt, p_fg, p_bg = ("HOLD" if hold else "LOCK"), (WARN if hold else GREEN), (WARN_DARK if hold else GREEN_DARK)
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

        psurf = self.f_xxs.render(p_txt, True, p_fg)
        pw = max(30, psurf.get_width() + 6)
        rx -= pw
        pygame.draw.rect(self.screen, p_bg, (rx, 3, pw, 13), border_radius=3)
        self.screen.blit(psurf, (rx + (pw - psurf.get_width()) // 2, 5))

        if self.audio.is_recording():
            rx -= 16
            pygame.draw.rect(self.screen, DANGER_DARK, (rx, 3, 14, 13), border_radius=3)
            pygame.draw.circle(self.screen, DANGER, (rx + 7, 9), 3)

        if self.audio.muted:
            rx -= 13
            pygame.draw.rect(self.screen, DANGER_DARK, (rx, 3, 11, 13), border_radius=2)
            self.screen.blit(self.f_xxs.render("M", True, DANGER), (rx + 3, 5))

    def draw_footer(self, hint: str) -> None:
        pygame.draw.rect(self.screen, SURFACE, (0, H - 14, W, 14))
        pygame.draw.line(self.screen, BORDER, (0, H - 14), (W, H - 14))
        self.screen.blit(self.f_xxs.render(hint[:56], True, SUBTEXT), (5, H - 11))

    # --- LIVE SCREEN ROUTER ---------------------------------------------------
    def draw_live(self) -> None:
        am = self.engine.config["app_mode"]
        if am == "VOICE":
            self._draw_live_voice()
        elif am == "ANALOG":
            self._draw_live_analog()
        elif am == "PAGER":
            self._draw_live_pager()
        elif am == "APRS":
            self._draw_live_aprs()
        elif am == "ISM433":
            self._draw_live_ism()
        elif am == "SONDE":
            self._draw_live_sonde()

    def _draw_freq_card(self, freq_hz: int, badge_txt: str, badge_col, sub_label: str, locked: bool = False) -> None:
        pygame.draw.rect(self.screen, SURFACE, (4, 22, W - 8, 28), border_radius=4)
        pygame.draw.rect(self.screen, MINT if locked else BORDER, (4, 22, W - 8, 28), 1, border_radius=4)
        f_txt = self.f_xl.render(fmt_mhz(freq_hz), True, TEXT)
        self.screen.blit(f_txt, (10, 26))
        self.screen.blit(self.f_xs_b.render("MHz", True, MINT), (10 + f_txt.get_width() + 3, 34))
        bs = self.f_xxs.render(badge_txt, True, badge_col)
        bw = bs.get_width() + 8
        pygame.draw.rect(self.screen, ELEVATED, (W - 8 - bw, 24, bw, 12), border_radius=3)
        self.screen.blit(bs, (W - 8 - bw + 4, 26))
        ls = self.f_xs.render(sub_label[:24], True, SUBTEXT)
        self.screen.blit(ls, (W - 8 - ls.get_width(), 38))

    def _draw_live_voice(self) -> None:
        with self.engine.lock:
            vscan = self.engine.config["voice_scan"]
            phase = self.engine.phase
            freq_hz = self.engine.active_freq_hz
            label = self.engine.active_label
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
        bcol = MINT if vscan == "AUTO" else (CYAN if vscan == "LIST" else WARN)
        self._draw_freq_card(freq_hz, vscan, bcol, label or "Digital Voice", locked=(phase == "LOCKED"))

        if phase == "LOCKED" or (vscan == "TUNER" and dsd.running):
            self._draw_voice_cards(dsd, hold, status)
        else:
            self._draw_spectrum_panel(freqs, psd, nf, thr, peaks, phase, status, found, skipped)

        self.draw_footer("Q:Mode SPC:Hold M:Scan R:Rec F:Freq S:Save B:Skip")

    def _draw_voice_cards(self, dsd: DsdState, hold: bool, status: str) -> None:
        proto = dsd.protocol or "DMR"
        sub = f" {dsd.submode}" if dsd.submode else ""
        cc = dsd.color_code if dsd.color_code else "-"
        x = 6
        x += self._pill(x, 53, f"{proto}{sub}", GREEN if dsd.synced else SUBTEXT, GREEN_DARK if dsd.synced else ELEVATED) + 4
        cc_lbl = "CC" if "DMR" in proto else ("CAN" if proto == "M17" else "ID")
        x += self._pill(x, 53, f"{cc_lbl}:{cc}", CYAN, CYAN_DARK) + 4
        enc = dsd.slot1.encrypted or dsd.slot2.encrypted
        x += self._pill(x, 53, "ENC" if enc else "CLEAR", DANGER if enc else MINT, DANGER_DARK if enc else MINT_DARK) + 4
        if self.audio.is_recording():
            sec = int(max(0, time.time() - self.audio.rec_started_at))
            x += self._pill(x, 53, f"REC {sec // 60}:{sec % 60:02d}", DANGER, DANGER_DARK) + 4
        if hold:
            self._pill(x, 53, "HOLD", WARN, WARN_DARK)

        is_dmr = (proto == "" or "DMR" in proto)
        if is_dmr:
            for idx, slot in enumerate((dsd.slot1, dsd.slot2)):
                cx = 6 + idx * 156
                cw, cy, ch = 152, 69, 66
                is_v = slot.voice
                pygame.draw.rect(self.screen, (12, 34, 28) if is_v else SURFACE, (cx, cy, cw, ch), border_radius=4)
                pygame.draw.rect(self.screen, GREEN if is_v else (MINT_DIM if slot.active else BORDER), (cx, cy, cw, ch), 1, border_radius=4)

                self.screen.blit(self.f_xs_b.render(f"SLOT {idx + 1}", True, GREEN if is_v else SUBTEXT), (cx + 5, cy + 3))
                if is_v:
                    self._pill(cx + cw - 40, cy + 2, "VOICE", GREEN, GREEN_DARK)

                # Talkgroup + BrandMeister name
                tg_name = self.engine.db.lookup_tg(slot.tg) if slot.tg else ""
                tg_str = f"TG {slot.tg}" + (f" {tg_name}" if tg_name else "") if slot.tg else "TG ---"
                self.screen.blit(self.f_sm_b.render(tg_str[:21], True, TEXT if slot.tg else MUTED), (cx + 5, cy + 17))

                # Callsign + Name lookup from RadioID.net SQLite DB!
                op = self.engine.db.lookup_id(slot.src) if slot.src else None
                if op and op["callsign"]:
                    line1 = op["callsign"] + (f" {op['first_name']}" if op["first_name"] else "")
                    self.screen.blit(self.f_sm_b.render(line1[:21], True, CYAN), (cx + 5, cy + 31))
                    loc = ", ".join(p for p in (op["city"], op["country"]) if p) or f"ID:{slot.src}"
                    self.screen.blit(self.f_xxs.render(loc[:24], True, PINK if op["country"] else SUBTEXT), (cx + 5, cy + 45))
                else:
                    src_str = f"ID: {slot.src}" if slot.src else "ID: ---"
                    self.screen.blit(self.f_mono.render(src_str[:20], True, CYAN if slot.src else MUTED), (cx + 5, cy + 32))
                    if slot.talker_alias:
                        self.screen.blit(self.f_xxs.render(slot.talker_alias[:23], True, WARN), (cx + 5, cy + 46))
                    elif slot.encrypted:
                        self.screen.blit(self.f_xxs.render(f"ENC {slot.enc_label}", True, DANGER), (cx + 5, cy + 46))

                if op and slot.src:
                    self.screen.blit(self.f_xxs.render(f"ID:{slot.src}"[:22], True, MUTED), (cx + 5, cy + 55))
        else:
            # Single-slot wide card for YSF / D-STAR / M17 / NXDN / P25 / dPMR
            s = dsd.slot1
            cx, cy, cw, ch = 6, 69, W - 12, 66
            is_v = s.voice
            pygame.draw.rect(self.screen, (14, 34, 32) if is_v else SURFACE, (cx, cy, cw, ch), border_radius=4)
            pygame.draw.rect(self.screen, GREEN if is_v else BORDER, (cx, cy, cw, ch), 1, border_radius=4)
            self.screen.blit(self.f_xs_b.render(f"{proto} CHANNEL", True, GREEN if is_v else MINT), (cx + 8, cy + 5))
            if is_v:
                self._pill(cx + cw - 44, cy + 4, "VOICE", GREEN, GREEN_DARK)
            src_lbl = f"SRC: {s.src or '---'}"
            if s.src and s.src.isdigit():
                op = self.engine.db.lookup_id(s.src)
                if op and op["callsign"]:
                    src_lbl = f"{op['callsign']} {op['first_name']} ({op['country']})"
            self.screen.blit(self.f_md.render(src_lbl[:34], True, TEXT), (cx + 8, cy + 22))
            dst_lbl = f"DST/TG: {s.dst or s.tg or '---'}   {dsd.extra_info}"
            self.screen.blit(self.f_mono.render(dst_lbl[:46], True, CYAN), (cx + 8, cy + 44))

        pygame.draw.rect(self.screen, ELEVATED, (6, 138, W - 12, 14), border_radius=3)
        raw_txt = dsd.raw_lines[-1] if dsd.raw_lines else status
        self.screen.blit(self.f_xxs.render(raw_txt[:54], True, SUBTEXT), (10, 141))

    def _draw_live_analog(self) -> None:
        with self.engine.lock:
            freq_hz = self.engine.active_freq_hz
            mod = str(self.engine.config.get("analog_mod", "WFM"))
            sq = int(self.engine.config.get("analog_squelch", 0))
            scanning = bool(self.engine.config.get("analog_scan", True))
            phase = self.engine.phase
            hold = self.engine.hold
            snr = self.engine.active_snr_db
            label = self.engine.active_label
            status = self.engine.status_text
            freqs = self.engine.sweep_freqs
            psd = self.engine.sweep_psd
            nf = self.engine.sweep_noise
            peaks = list(self.engine.sweep_peaks)
            thr = float(self.engine.config["threshold_db"])

        badge = f"{mod}:AUTO" if scanning else f"{mod}:TUNE"
        self._draw_freq_card(freq_hz, badge, PINK, label or f"Analog {mod}", locked=(phase == "LOCKED"))
        if scanning and phase != "LOCKED":
            self._draw_spectrum_panel(freqs, psd, nf, thr, peaks, phase, status, 0, 0)
        else:
            cx, cy, cw, ch = 6, 54, W - 12, 96
            pygame.draw.rect(self.screen, SURFACE, (cx, cy, cw, ch), border_radius=4)
            pygame.draw.rect(self.screen, GREEN if phase == "LOCKED" else (PINK if self.engine.analog.running else BORDER), (cx, cy, cw, ch), 1, border_radius=4)
            x = cx + 8
            x += self._pill(x, cy + 8, f"MOD: {mod}", PINK, PINK_DARK) + 5
            x += self._pill(x, cy + 8, f"SQ: {sq}", CYAN, CYAN_DARK) + 5
            if phase == "LOCKED" and snr > 0:
                x += self._pill(x, cy + 8, f"+{snr:.0f}dB", GREEN, GREEN_DARK) + 5
            if hold:
                x += self._pill(x, cy + 8, "HOLD", WARN, WARN_DARK) + 5
            if self.audio.is_recording():
                sec = int(max(0, time.time() - self.audio.rec_started_at))
                self._pill(x, cy + 8, f"REC {sec // 60}:{sec % 60:02d}", DANGER, DANGER_DARK)
            headline = f"Locked: {label}" if phase == "LOCKED" else ("Analog Audio Active" if self.engine.analog.running else status)
            self.screen.blit(self.f_sm_b.render(headline[:38], True, TEXT), (cx + 8, cy + 32))
            step_lbl = "100kHz" if mod == "WFM" else ("25kHz" if mod == "AM" else "12.5kHz")
            self.screen.blit(self.f_mono.render(f"Step: LEFT/RIGHT +/-{step_lbl}  B:Next Station", True, SUBTEXT), (cx + 8, cy + 54))
            self.screen.blit(self.f_mono.render("SPC:Hold  M:Auto/Tune  A:NFM/AM/WFM  R:Rec", True, MINT), (cx + 8, cy + 72))

        self.draw_footer("Q:Mode SPC:Hold M:Auto/Tune A:Mod B:Next R:Rec")

    def _draw_live_pager(self) -> None:
        with self.engine.lock:
            freq_hz = self.engine.active_freq_hz
        msgs = self.engine.pager.get_messages()
        self._draw_freq_card(freq_hz, "POCSAG", WARN, f"{len(msgs)} messages", locked=bool(msgs))

        if not msgs:
            pygame.draw.rect(self.screen, SURFACE, (6, 54, W - 12, 96), border_radius=4)
            self.screen.blit(self.f_sm.render("Listening for POCSAG / FLEX pages...", True, SUBTEXT), (42, 86))
            self.screen.blit(self.f_xxs.render("Tip: DAPNET DE = 439.9875 MHz (2:BAND)", True, MINT), (44, 106))
        else:
            self.feed_scroll = max(0, min(len(msgs) - 1, self.feed_scroll))
            vis = 3
            start = max(0, min(max(0, len(msgs) - vis), self.feed_scroll))
            y = 54
            for i in range(start, min(len(msgs), start + vis)):
                m = msgs[i]
                sel = i == self.feed_scroll
                pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (6, y, W - 12, 30), border_radius=3)
                if sel:
                    pygame.draw.rect(self.screen, WARN, (6, y, W - 12, 30), 1, border_radius=3)
                ts = datetime.datetime.fromtimestamp(m["ts"]).strftime("%H:%M:%S")
                hdr = f"{ts}  {m['proto']}  RIC:{m['ric']}"
                self.screen.blit(self.f_xxs.render(hdr[:48], True, WARN), (10, y + 3))
                self.screen.blit(self.f_xs.render(m["text"][:48], True, TEXT), (10, y + 15))
                y += 32

        self.draw_footer("Q:Mode UP/DN:Scroll ENT:Read F:Freq 2:Presets")

    def _draw_live_aprs(self) -> None:
        with self.engine.lock:
            freq_hz = self.engine.active_freq_hz
        pkts = self.engine.aprs.get_packets()
        self._draw_freq_card(freq_hz, "APRS", CYAN, f"{len(pkts)} packets", locked=bool(pkts))

        if not pkts:
            pygame.draw.rect(self.screen, SURFACE, (6, 54, W - 12, 96), border_radius=4)
            self.screen.blit(self.f_sm.render("Listening for APRS AFSK1200 packets...", True, SUBTEXT), (40, 86))
            self.screen.blit(self.f_xxs.render("EU: 144.800 MHz | ISS: 145.825 MHz (2:BAND)", True, CYAN), (32, 106))
        else:
            self.feed_scroll = max(0, min(len(pkts) - 1, self.feed_scroll))
            vis = 3
            start = max(0, min(max(0, len(pkts) - vis), self.feed_scroll))
            y = 54
            for i in range(start, min(len(pkts), start + vis)):
                p = pkts[i]
                sel = i == self.feed_scroll
                pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (6, y, W - 12, 30), border_radius=3)
                if sel:
                    pygame.draw.rect(self.screen, CYAN, (6, y, W - 12, 30), 1, border_radius=3)
                ts = datetime.datetime.fromtimestamp(p["ts"]).strftime("%H:%M:%S")
                hdr = f"{ts}  {p['src']}" + (f"  {p['pos']}" if p["pos"] else "")
                self.screen.blit(self.f_xxs.render(hdr[:52], True, CYAN), (10, y + 3))
                self.screen.blit(self.f_xs.render(p["text"][:48], True, TEXT), (10, y + 15))
                y += 32

        self.draw_footer("Q:Mode UP/DN:Scroll ENT:Read F:Freq 2:Presets")

    def _draw_live_ism(self) -> None:
        with self.engine.lock:
            freq_hz = self.engine.active_freq_hz
        sensors = self.engine.ism.get_sensors()
        self._draw_freq_card(freq_hz, "433", GREEN, f"{len(sensors)} devices ({self.engine.ism.total_rx} rx)", locked=bool(sensors))

        if not sensors:
            pygame.draw.rect(self.screen, SURFACE, (6, 54, W - 12, 96), border_radius=4)
            self.screen.blit(self.f_sm.render("Listening with rtl_433 for sensors...", True, SUBTEXT), (42, 86))
            self.screen.blit(self.f_xxs.render("Weather stations, TPMS, remotes on 433.92 MHz", True, GREEN), (26, 106))
        else:
            self.feed_scroll = max(0, min(len(sensors) - 1, self.feed_scroll))
            vis = 4
            start = max(0, min(max(0, len(sensors) - vis), self.feed_scroll))
            y = 54
            for i in range(start, min(len(sensors), start + vis)):
                s = sensors[i]
                sel = i == self.feed_scroll
                pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (6, y, W - 12, 22), border_radius=3)
                if sel:
                    pygame.draw.rect(self.screen, GREEN, (6, y, W - 12, 22), 1, border_radius=3)
                age = int(max(0, time.time() - s["ts"]))
                self.screen.blit(self.f_xs_b.render(f"{s['model'][:17]} #{s['id']}"[:22], True, TEXT), (10, y + 2))
                self.screen.blit(self.f_xxs.render(f"{age}s x{s['hits']}", True, MUTED), (W - 58, y + 3))
                self.screen.blit(self.f_xxs.render(s["summary"][:46], True, GREEN), (10, y + 12))
                y += 24

        self.draw_footer("Q:Mode UP/DN:Scroll F:Freq 2:Presets")

    def _draw_live_sonde(self) -> None:
        with self.engine.lock:
            freq_hz = self.engine.active_freq_hz
            phase = self.engine.phase
            status = self.engine.status_text
            do_sweep = bool(self.engine.config.get("sonde_sweep", True))
            freqs = self.engine.sweep_freqs
            psd = self.engine.sweep_psd
            nf = self.engine.sweep_noise
            peaks = list(self.engine.sweep_peaks)
            thr = float(self.engine.config["threshold_db"])

        lat, raw = self.engine.sonde.get_latest()
        self._draw_freq_card(freq_hz, "RS41", (180, 130, 255), "400-406 Balloon", locked=bool(lat))

        if do_sweep and phase == "SWEEP" and not lat:
            self._draw_spectrum_panel(freqs, psd, nf, thr, peaks, phase, status, 0, 0)
        elif lat:
            cx, cy, cw, ch = 6, 54, W - 12, 96
            pygame.draw.rect(self.screen, SURFACE, (cx, cy, cw, ch), border_radius=4)
            pygame.draw.rect(self.screen, (180, 130, 255), (cx, cy, cw, ch), 1, border_radius=4)
            self.screen.blit(self.f_md.render(f"SONDE {lat['sonde_id']}  #{lat['frame']}", True, (200, 160, 255)), (cx + 8, cy + 6))
            self.screen.blit(self.f_lg.render(f"ALT: {lat['alt_m']} m", True, GREEN), (cx + 8, cy + 26))
            vel = f"vV:{lat['vv']}m/s  vH:{lat['vh']}m/s  T:{lat['temp_c']}C"
            self.screen.blit(self.f_mono.render(vel[:46], True, CYAN), (cx + 8, cy + 50))
            self.screen.blit(self.f_mono.render(f"GPS: {lat['lat']}, {lat['lon']}", True, TEXT), (cx + 8, cy + 68))
        else:
            pygame.draw.rect(self.screen, SURFACE, (6, 54, W - 12, 96), border_radius=4)
            self.screen.blit(self.f_sm.render(status[:42], True, SUBTEXT), (14, 78))
            if raw:
                self.screen.blit(self.f_xxs.render(raw[:50], True, MINT), (14, 102))

        self.draw_footer("Q:Mode M:Sweep/Tune F:Freq B:Skip 2:Presets")

    # --- SPECTRUM PANEL (PIXEL-EXACT PEAK ARROW ALIGNMENT!) -------------------
    def _draw_spectrum_panel(
        self,
        freqs: np.ndarray | None,
        psd: np.ndarray | None,
        nf: float,
        thr: float,
        peaks: list[PeakCandidate],
        phase: str,
        status: str,
        found: int,
        skipped: int,
    ) -> None:
        px, py, pw, ph = 6, 54, W - 12, 76
        pygame.draw.rect(self.screen, SURFACE, (px, py, pw, ph), border_radius=4)
        pygame.draw.rect(self.screen, BORDER, (px, py, pw, ph), 1, border_radius=4)

        if freqs is not None and psd is not None and len(psd) > 8:
            cols = pw - 4
            n_bins = len(psd)
            if n_bins >= cols:
                usable = (n_bins // cols) * cols
                col_vals = psd[:usable].reshape(cols, -1).max(axis=1)
                f0 = float(freqs[0])
                f1 = float(freqs[usable - 1])  # Matches exact rendered slice!
            else:
                col_vals = np.interp(np.linspace(0, n_bins - 1, cols), np.arange(n_bins), psd)
                f0 = float(freqs[0])
                f1 = float(freqs[-1])

            y_min = nf - 5.0
            y_max = max(nf + thr + 22.0, float(np.max(col_vals)) + 3.0)
            span = max(10.0, y_max - y_min)

            thr_y = py + ph - 12 - int(((nf + thr - y_min) / span) * (ph - 16))
            thr_y = max(py + 3, min(py + ph - 13, thr_y))
            for gx in range(px + 2, px + pw - 2, 6):
                pygame.draw.line(self.screen, WARN_DARK, (gx, thr_y), (min(gx + 3, px + pw - 3), thr_y))

            base_y = py + ph - 12
            for i in range(cols):
                v = float(col_vals[i])
                h_px = int(max(1, min(ph - 16, ((v - y_min) / span) * (ph - 16))))
                x = px + 2 + i
                col = MINT if v >= (nf + thr) else (44, 114, 168)
                pygame.draw.line(self.screen, col, (x, base_y), (x, base_y - h_px))

            # Pixel-exact centered orange peak arrow over the rendered green column!
            fspan = max(1.0, f1 - f0)
            for pk in peaks[:5]:
                frac = (pk.freq_hz - f0) / fspan
                if 0.0 <= frac <= 1.0:
                    rx = px + 2 + int(round(frac * (cols - 1)))
                    pygame.draw.polygon(self.screen, WARN, [(rx, py + 9), (rx - 3, py + 3), (rx + 3, py + 3)])

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

        pygame.draw.rect(self.screen, ELEVATED, (6, 134, W - 12, 17), border_radius=3)
        st_col = WARN if phase == "CHECK" else (DANGER if phase == "ERROR" else TEXT)
        self.screen.blit(self.f_xxs.render(status[:39], True, st_col), (10, 138))
        cnt = self.f_xxs.render(f"HIT:{found} SKP:{skipped}", True, MINT)
        self.screen.blit(cnt, (W - 10 - cnt.get_width(), 138))

    # --- TAB 2: BAND / PRESETS ------------------------------------------------
    def draw_bands(self) -> None:
        am = self.engine.config["app_mode"]
        if am == "VOICE":
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
                self.screen.blit(self.f_mono_b.render("[x]" if en else "[ ]", True, MINT if en else MUTED), (8, y + 4))
                self.screen.blit(self.f_xs_b.render(b["name"][:18], True, TEXT if en else SUBTEXT), (34, y + 5))
                rng = f"{b['start_hz']/1e6:.2f}-{b['end_hz']/1e6:.2f}"
                rs = self.f_xxs.render(rng, True, CYAN if en else MUTED)
                self.screen.blit(rs, (W - 10 - rs.get_width(), y + 6))
                y += 21
            self.draw_footer("SPC/ENT:Toggle  E:Edit Band  A:Start AUTO Scan")
            return

        if am == "ANALOG":
            bands = self.engine.config.get("analog_bands", DEFAULT_ANALOG_BANDS)
            vis = 6
            self.band_sel = max(0, min(len(bands) - 1, self.band_sel))
            start = max(0, min(max(0, len(bands) - vis), self.band_sel - vis // 2))
            y = 24
            for i in range(start, min(len(bands), start + vis)):
                b = bands[i]
                sel = i == self.band_sel
                en = bool(b.get("enabled", False))
                pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (4, y, W - 8, 20), border_radius=3)
                if sel:
                    pygame.draw.rect(self.screen, PINK, (4, y, W - 8, 20), 1, border_radius=3)
                self.screen.blit(self.f_mono_b.render("[x]" if en else "[ ]", True, PINK if en else MUTED), (8, y + 4))
                self.screen.blit(self.f_xs_b.render(b["name"][:18], True, TEXT if en else SUBTEXT), (34, y + 5))
                rng = f"{b['start_hz']/1e6:.1f}-{b['end_hz']/1e6:.1f} {b['mod']}"
                rs = self.f_xxs.render(rng, True, CYAN if en else MUTED)
                self.screen.blit(rs, (W - 10 - rs.get_width(), y + 6))
                y += 21
            self.draw_footer("SPC/ENT:Toggle  A:Start AUTO Scan  T:Direct Tune")
            return

        presets = self._current_mode_presets()
        vis = 6
        self.band_sel = max(0, min(max(0, len(presets) - 1), self.band_sel))
        start = max(0, min(max(0, len(presets) - vis), self.band_sel - vis // 2))
        y = 24
        for i in range(start, min(len(presets), start + vis)):
            p = presets[i]
            sel = i == self.band_sel
            pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (4, y, W - 8, 20), border_radius=3)
            if sel:
                pygame.draw.rect(self.screen, MINT, (4, y, W - 8, 20), 1, border_radius=3)
            self.screen.blit(self.f_xs_b.render(p["name"][:24], True, TEXT if sel else SUBTEXT), (10, y + 5))
            rtxt = "400-406" if p["freq_hz"] == 0 else f"{fmt_mhz(p['freq_hz'])} {p.get('mod', '')}"
            rs = self.f_xxs.render(rtxt.strip(), True, CYAN)
            self.screen.blit(rs, (W - 10 - rs.get_width(), y + 6))
            y += 21
        self.draw_footer("ENT:Select Preset  Q:Switch Mode")

    # --- TAB 3: SAVED LIST ----------------------------------------------------
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
                self.screen.blit(self.f_mono_b.render(fmt_mhz(int(ch["freq_hz"])), True, TEXT), (8, y + 4))
                self.screen.blit(self.f_xs.render(str(ch.get("name", ""))[:15], True, SUBTEXT), (78, y + 5))
                meta = ""
                if ch.get("cc"):
                    meta += f"CC{ch['cc']} "
                if ch.get("last_tg"):
                    meta += f"TG{ch['last_tg']}"
                if not meta:
                    meta = str(ch.get("source", ""))
                ms = self.f_xxs.render(meta[:14], True, MINT if ch.get("source") == "AUTO" else CYAN)
                self.screen.blit(ms, (W - 10 - ms.get_width(), y + 6))
                y += 21
        self.draw_footer("ENT:Tune  S:Scan List  N:Add  D:Del  B:Blacklist")

    # --- TAB 4: PER-MODE LOG & RECORDINGS PLAYBACK ----------------------------
    def draw_log(self) -> None:
        am = self.engine.config["app_mode"]
        mode_col = dict((m[0], m[3]) for m in APP_MODES).get(am, MINT)
        logs = self.engine.get_mode_log(am)
        recs = self._list_recordings()

        pygame.draw.rect(self.screen, ELEVATED, (4, 22, W - 8, 16), border_radius=3)
        e_col = mode_col if self.log_view == "EVENTS" else SUBTEXT
        r_col = PINK if self.log_view == "RECS" else SUBTEXT
        self.screen.blit(self.f_xs_b.render(f"{am} LOG ({len(logs)})", True, e_col), (10, 24))
        self.screen.blit(self.f_xs_b.render(f"RECORDINGS ({len(recs)})", True, r_col), (132, 24))
        if self.audio.is_playing():
            self._pill(W - 60, 23, "PLAYING", GREEN, GREEN_DARK)

        if self.log_view == "RECS":
            if not recs:
                msg = self.f_sm.render("No recordings yet. Press R in LIVE to record.", True, SUBTEXT)
                self.screen.blit(msg, (W // 2 - msg.get_width() // 2, 82))
            else:
                self.log_sel = max(0, min(len(recs) - 1, self.log_sel))
                vis = 5
                start = max(0, min(max(0, len(recs) - vis), self.log_sel - vis // 2))
                y = 42
                for i in range(start, min(len(recs), start + vis)):
                    fn = recs[i]
                    sel = i == self.log_sel
                    pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (4, y, W - 8, 21), border_radius=3)
                    if sel:
                        pygame.draw.rect(self.screen, PINK, (4, y, W - 8, 21), 1, border_radius=3)
                    try:
                        kb = os.path.getsize(os.path.join(REC_DIR, fn)) // 1024
                    except Exception:
                        kb = 0
                    self.screen.blit(self.f_xxs.render(fn[:38], True, TEXT), (8, y + 6))
                    ks = self.f_xxs.render(f"{kb}KB", True, PINK)
                    self.screen.blit(ks, (W - 10 - ks.get_width(), y + 6))
                    y += 22
            self.draw_footer(f"L/←→:{am} Log  ENT:Play WAV  SPC:Stop  D:Del")
            return

        if not logs:
            msg = self.f_sm.render(f"No {am} events logged yet.", True, SUBTEXT)
            self.screen.blit(msg, (W // 2 - msg.get_width() // 2, 82))
        else:
            self.log_sel = max(0, min(len(logs) - 1, self.log_sel))
            vis = 5
            start = max(0, min(max(0, len(logs) - vis), self.log_sel - vis // 2))
            y = 42
            for i in range(start, min(len(logs), start + vis)):
                ev = logs[i]
                sel = i == self.log_sel
                pygame.draw.rect(self.screen, ELEVATED if sel else SURFACE, (4, y, W - 8, 21), border_radius=3)
                if sel:
                    pygame.draw.rect(self.screen, mode_col, (4, y, W - 8, 21), 1, border_radius=3)
                ts = datetime.datetime.fromtimestamp(ev.get("ts", 0)).strftime("%H:%M:%S")
                self.screen.blit(self.f_xxs.render(ts, True, MUTED), (8, y + 6))
                kind = ev.get("kind", am)
                if kind == "PAGER":
                    txt = f"RIC {ev.get('ric', '')}: {ev.get('text', '')}"
                    self.screen.blit(self.f_xxs.render(txt[:42], True, WARN), (54, y + 6))
                elif kind == "APRS":
                    txt = f"{ev.get('src', '')}: {ev.get('text', '')}"
                    self.screen.blit(self.f_xxs.render(txt[:42], True, CYAN), (54, y + 6))
                elif kind == "ISM433":
                    txt = f"{ev.get('model', '')[:14]} {ev.get('summary', '')}"
                    self.screen.blit(self.f_xxs.render(txt[:42], True, GREEN), (54, y + 6))
                elif kind == "ANALOG":
                    txt = f"{fmt_mhz(int(ev.get('freq_hz', 0)))} {ev.get('mod', '')} +{ev.get('snr', 0):.0f}dB"
                    self.screen.blit(self.f_xxs.render(txt[:42], True, PINK), (54, y + 6))
                elif kind == "SONDE":
                    txt = f"RS41 {ev.get('sonde_id', '')} {ev.get('alt_m', '')}m"
                    self.screen.blit(self.f_xxs.render(txt[:42], True, (180, 130, 255)), (54, y + 6))
                else:
                    who = ev.get("callsign") or ev.get("src") or "-"
                    tg = ev.get("tg") or "-"
                    txt = f"{fmt_mhz(int(ev.get('freq_hz', 0)))} TG:{tg} {who}"
                    self.screen.blit(self.f_xxs.render(txt[:42], True, GREEN if not ev.get("encrypted") else DANGER), (54, y + 6))
                y += 22
        self.draw_footer(f"L/←→:Recs  ENT:Tune/Read  C:Clear {am} Log")

    # --- TAB 5: CFG + CALLSIGN DB DOWNLOADER ----------------------------------
    def draw_cfg(self) -> None:
        cfg = self.engine.config
        db = self.engine.db
        if db.downloading:
            db_val = f"{db.dl_stage[:14]} {int(db.dl_pct * 100)}%"
        elif db.user_count > 0:
            db_val = f"{db.user_count // 1000}k IDs / {db.tg_count} TG"
        else:
            db_val = "ENT: Download (16MB)"

        mode_name = dict(DECODER_MODES).get(cfg["decoder_flag"], cfg["decoder_flag"])
        gain_str = "AUTO" if int(cfg["gain_db"]) == 0 else f"{int(cfg['gain_db'])} dB"
        items = [
            ("Callsign+TG DB", db_val),
            ("Digital Decoder", mode_name),
            ("WAV Recording (R)", "REC ON" if self.audio.is_recording() else "OFF"),
            ("RTL Gain", gain_str),
            ("RTL Bandwidth", f"{int(cfg.get('bw_khz', 12))} kHz"),
            ("Analog Squelch", str(int(cfg.get("analog_squelch", 28)))),
            ("PPM Correction", f"{int(cfg['ppm']):+d} ppm"),
            ("Peak Threshold", f"+{float(cfg['threshold_db']):.0f} dB"),
            ("Verify Timeout", f"{float(cfg['verify_sec']):.1f} s"),
            ("Call Hang Time", f"{float(cfg['hang_sec']):.1f} s"),
            ("Auto-Save DMR", "ON" if cfg.get("auto_save_found", True) else "OFF"),
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
            # Progress bar behind DB row when downloading
            if i == 0 and db.downloading:
                pw = int((W - 10) * max(0.02, min(1.0, db.dl_pct)))
                pygame.draw.rect(self.screen, MINT_DARK, (5, y + 1, pw, 18), border_radius=2)
            self.screen.blit(self.f_xs_b.render(k, True, TEXT if sel else SUBTEXT), (10, y + 5))
            vcol = PINK if (i == 0 and db.user_count == 0 and not db.downloading) else (MINT if sel else CYAN)
            vs = self.f_mono_b.render(v[:22], True, vcol)
            self.screen.blit(vs, (W - 10 - vs.get_width(), y + 4))
            y += 21

        self.draw_footer("UP/DN:Select  LEFT/RIGHT:Change  ENT:Download/Act")

    # --- MODALS (QUICK-MODE 2x3 GRID + FREQ + BAND EDIT + MSG DETAIL) ---------
    def draw_modal(self) -> None:
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((4, 5, 10, 205))
        self.screen.blit(overlay, (0, 0))

        if self.modal == "MODE_GRID":
            bx, by, bw, bh = 12, 16, W - 24, H - 32
            pygame.draw.rect(self.screen, SURFACE, (bx, by, bw, bh), border_radius=6)
            pygame.draw.rect(self.screen, PINK, (bx, by, bw, bh), 1, border_radius=6)
            self.screen.blit(self.f_sm_b.render("SELECT RECEIVER MODE (1-6)", True, TEXT), (bx + 10, by + 6))

            cur_am = self.engine.config["app_mode"]
            cw, ch = 90, 44
            for idx, (mid, title, sub, col) in enumerate(APP_MODES):
                col_i = idx % 3
                row_i = idx // 3
                tx = bx + 10 + col_i * (cw + 5)
                ty = by + 24 + row_i * (ch + 6)
                sel = idx == self.mode_grid_sel
                is_cur = mid == cur_am
                pygame.draw.rect(self.screen, ELEVATED if (sel or is_cur) else BG, (tx, ty, cw, ch), border_radius=5)
                pygame.draw.rect(self.screen, col if sel else (BORDER if not is_cur else MINT_DIM), (tx, ty, cw, ch), 2 if sel else 1, border_radius=5)
                self.screen.blit(self.f_sm_b.render(f"{idx+1} {title}", True, col if sel else TEXT), (tx + 6, ty + 7))
                self.screen.blit(self.f_xxs.render(sub[:13], True, SUBTEXT), (tx + 6, ty + 26))

            self.screen.blit(self.f_xxs.render("1-6 or Arrows+ENT: Switch   ESC: Cancel", True, SUBTEXT), (bx + 10, by + bh - 13))

        elif self.modal in ("FREQ_INPUT", "ADD_CHAN"):
            bx, by, bw, bh = 36, 44, 248, 80
            pygame.draw.rect(self.screen, SURFACE, (bx, by, bw, bh), border_radius=6)
            pygame.draw.rect(self.screen, MINT, (bx, by, bw, bh), 1, border_radius=6)
            title = "Direct Tune (MHz)" if self.modal == "FREQ_INPUT" else "Add Channel (MHz)"
            self.screen.blit(self.f_sm_b.render(title, True, MINT), (bx + 12, by + 10))
            pygame.draw.rect(self.screen, BG, (bx + 12, by + 30, bw - 24, 26), border_radius=4)
            pygame.draw.rect(self.screen, BORDER, (bx + 12, by + 30, bw - 24, 26), 1, border_radius=4)
            self.screen.blit(self.f_lg.render(self.input_buf + "_", True, TEXT), (bx + 18, by + 35))
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

        elif self.modal == "MSG_DETAIL":
            bx, by, bw, bh = 16, 22, W - 32, H - 44
            pygame.draw.rect(self.screen, SURFACE, (bx, by, bw, bh), border_radius=6)
            pygame.draw.rect(self.screen, WARN, (bx, by, bw, bh), 1, border_radius=6)
            self.screen.blit(self.f_sm_b.render(self.detail_title[:32], True, WARN), (bx + 10, by + 8))
            # Wrap text into 42-char lines
            body = self.detail_body
            lines = [body[i:i + 42] for i in range(0, len(body), 42)][:7]
            for idx, ln in enumerate(lines):
                self.screen.blit(self.f_mono.render(ln, True, TEXT), (bx + 10, by + 28 + idx * 12))
            self.screen.blit(self.f_xxs.render("ENT / ESC: Close", True, SUBTEXT), (bx + 10, by + bh - 14))

    def draw_toast(self) -> None:
        if self.toast_msg and time.time() < self.toast_until:
            surf = self.f_xs_b.render(self.toast_msg[:44], True, TEXT)
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
        pygame.draw.rect(self.screen, DANGER, (bar_x, bar_y, max(2, int(bar_w * pct)), bar_h), border_radius=3)

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

    def _populate_preview_state(self, mode: str) -> None:
        # Realistic mock state for 320x170 AppStore & README screenshots
        self.engine.db.user_count = 317420
        self.engine.db.tg_count = 1847
        self.engine.db._cache_id[3199020] = {
            "callsign": "WX9USA", "first_name": "Richard", "last_name": "",
            "city": "Clinton", "state": "Illinois", "country": "United States",
        }
        self.engine.db._cache_id[2624511] = {
            "callsign": "DL1GURK", "first_name": "Stefan", "last_name": "",
            "city": "Munich", "state": "Bavaria", "country": "Germany",
        }
        self.engine.db._cache_tg[91] = "World-wide"
        self.engine.db._cache_tg[262] = "Deutschland"

        # Synthetic realistic 1.8 MHz RF spectrum around 438.8 MHz
        freqs = np.linspace(437_900_000, 439_700_000, 1536, dtype=np.float64)
        rng = np.random.default_rng(42)
        psd = -24.5 + rng.normal(0.0, 1.3, size=len(freqs)).astype(np.float32)
        for f_pk, amp, wid in (
            (438_425_000, 48.0, 7_500),
            (438_687_500, 16.0, 5_000),
            (439_125_000, 22.0, 8_000),
        ):
            psd += amp * np.exp(-0.5 * ((freqs - f_pk) / wid) ** 2).astype(np.float32)
        self.engine.sweep_freqs = freqs
        self.engine.sweep_psd = psd
        self.engine.sweep_noise = -24.5
        self.engine.sweep_peaks = [
            PeakCandidate(freq_hz=438_425_000, power_db=23.5, snr_db=48.0, bw_hz=7200),
            PeakCandidate(freq_hz=439_125_000, power_db=-2.5, snr_db=22.0, bw_hz=8100),
            PeakCandidate(freq_hz=438_687_500, power_db=-8.5, snr_db=16.0, bw_hz=5000),
        ]
        self.engine.found_count = 4
        self.engine.skipped_count = 2

        if mode == "dmr_live":
            self.tab_idx = 0
            self.engine.config["app_mode"] = "VOICE"
            self.engine.config["voice_scan"] = "AUTO"
            self.engine.phase = "LOCKED"
            self.engine.active_freq_hz = 438_425_000
            self.engine.active_label = "70cm Ham DMR/DV"
            self.engine.status_text = "DMR LOCK 438.4250"
            now = time.time()
            st = self.engine.dsd._state
            st.running = True
            st.synced = True
            st.last_sync_at = now
            st.last_voice_at = now
            st.protocol = "DMR"
            st.submode = "BS"
            st.color_code = "1"
            st.slot1.active = True
            st.slot1.voice = False
            st.slot1.tg = "262"
            st.slot1.src = "2624511"
            st.slot1.last_activity = now
            st.slot2.active = True
            st.slot2.voice = True
            st.slot2.tg = "91"
            st.slot2.src = "3199020"
            st.slot2.call_type = "GROUP"
            st.slot2.last_activity = now
            st.raw_lines.append("SLOT 2 TGT=91 SRC=3199020 Group Call | Color Code=01")
        elif mode == "spectrum":
            self.tab_idx = 0
            self.engine.config["app_mode"] = "VOICE"
            self.engine.config["voice_scan"] = "AUTO"
            self.engine.phase = "SWEEP"
            self.engine.active_freq_hz = 438_800_000
            self.engine.active_label = "70cm Ham DMR/DV"
            self.engine.status_text = "Sweep 70cm Ham DMR/DV: 438.800 MHz"
        elif mode in ("grid", "modes"):
            self.tab_idx = 0
            self.engine.phase = "SWEEP"
            self.engine.active_freq_hz = 438_425_000
            self.mode_grid_sel = 0
            self.modal = "MODE_GRID"
        elif mode == "config":
            self.tab_idx = 4
            self.cfg_sel = 0
            self.engine.phase = "SWEEP"

    def run(self) -> None:
        if self.preview_out:
            self._populate_preview_state(self.preview_mode or "dmr_live")
            self.render()
            pygame.image.save(self.screen, self.preview_out)
            return

        self.engine.start()
        last_render = 0.0
        try:
            while self.running:
                t0 = time.time()
                had_key = self.handle_events()
                with self.engine.lock:
                    phase = self.engine.phase
                min_interval = 0.05 if (had_key or self.esc_down_at is not None) else (0.18 if phase == "LOCKED" else 0.10)
                if had_key or (t0 - last_render) >= min_interval:
                    self.render()
                    self.flush_fb()
                    last_render = t0
                dt = time.time() - t0
                if dt < 0.04:
                    time.sleep(0.04 - dt)
        finally:
            self.audio.stop_recording()
            self.audio.stop_playback()
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
    p_out = sys.argv[2] if len(sys.argv) >= 3 and sys.argv[1] == "--preview" else None
    p_mode = sys.argv[3] if len(sys.argv) >= 4 else None
    ZeroWaveApp(preview_out=p_out, preview_mode=p_mode).run()
