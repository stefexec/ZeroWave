#!/usr/bin/env python3
"""
Multi-protocol DSD-FME subprocess controller & real-time ANSI log parser.
Supports DMR, YSF (C4FM), D-STAR, M17, dPMR, NXDN48/96, P25 Ph1/Ph2, Auto (-fa),
plus optional per-call WAV recording (-7 <dir> -P).
"""

from __future__ import annotations

import collections
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SYNC_RE = re.compile(
    r"Sync:\s*[+-]?(DMR|P25p[12]|NXDN\d*|dPMR|YSF|D-STAR|M17|EDACS|ProVoice|X2-TDMA)",
    re.IGNORECASE,
)
CC_RE = re.compile(r"(?:Color\s*Code|CC|NAC|RAN|CAN)\s*[=:]\s*([0-9A-Fa-f]+)", re.IGNORECASE)
SLOT_BRACKET_RE = re.compile(r"\[(slot1|slot2)\]", re.IGNORECASE)
SLOT_WORD_RE = re.compile(r"\bSLOT\s*([12])\b", re.IGNORECASE)
TG_RE = re.compile(r"\b(?:TG|TGT|Group)\s*[=:]\s*(\d+)", re.IGNORECASE)
SRC_RE = re.compile(r"\b(?:SRC|RID|Source)\s*[=:]\s*([A-Za-z0-9/_-]+)", re.IGNORECASE)
DST_RE = re.compile(r"\b(?:DST|DEST|UR|Target)\s*[=:]\s*([A-Za-z0-9/_-]+)", re.IGNORECASE)
ENC_RE = re.compile(
    r"\b(BP|EP|RC4|AES|DES|HYT|Basic Privacy|Enhanced Privacy|ALG\s*=\s*0x[0-9A-Fa-f]+|ENC)\b",
    re.IGNORECASE,
)
VOICE_RE = re.compile(
    r"(?:\bVLC\b|\bVC[1-6]\*?|\bVoice\s+LC\b|\bGroup\s+Call\b|\bPrivate\s+Call\b|\bAMBE\b|\bTalker\s+Alias\b|\bVD[12]\b|\bVW\b|\bM17\s+STR\b|\bLDU[12]\b)",
    re.IGNORECASE,
)
IDLE_RE = re.compile(r"\|\s*IDLE\b", re.IGNORECASE)
TERM_RE = re.compile(r"(Terminator\s+with\s+LC|\bTLC\b|\bEOT\b|\bTDU\b)", re.IGNORECASE)
TA_RE = re.compile(r"Talker\s*Alias[^:]*:\s*(.+)$", re.IGNORECASE)
MODE_TYPE_RE = re.compile(r"\b(DMR\s+BS|DMR\s+MS|DMR\s+Mono|BS\s+Data|MS\s+Voice|MS\s+Data)\b", re.IGNORECASE)
# YSF / D-STAR / M17 specific patterns
YSF_CALL_RE = re.compile(r"(?:Src|SRC|Callsign|From)\s*[:=]\s*([A-Z0-9]{3,10}(?:[-/][A-Z0-9]{1,4})?)", re.IGNORECASE)
YSF_DGID_RE = re.compile(r"DG-?ID\s*[:=]\s*(\d+)", re.IGNORECASE)
DSTAR_MY_RE = re.compile(r"MY:\s*([A-Z0-9/_ -]{3,12})", re.IGNORECASE)
DSTAR_UR_RE = re.compile(r"UR:\s*([A-Z0-9/_ -]{3,12})", re.IGNORECASE)
DSTAR_RPT_RE = re.compile(r"RPT[12]:\s*([A-Z0-9/_ -]{3,12})", re.IGNORECASE)
WAV_SAVED_RE = re.compile(r"(?:Per Call|WAV|Saving).*?([A-Za-z0-9_./-]+\.wav)", re.IGNORECASE)


@dataclass
class SlotState:
    active: bool = False
    voice: bool = False
    tg: str = ""
    src: str = ""
    dst: str = ""
    call_type: str = ""
    encrypted: bool = False
    enc_label: str = ""
    talker_alias: str = ""
    last_activity: float = 0.0


@dataclass
class DsdState:
    running: bool = False
    recording: bool = False
    freq_hz: int = 0
    started_at: float = 0.0
    protocol: str = ""      # DMR, YSF, D-STAR, M17, P25p1, NXDN48, dPMR
    submode: str = ""       # BS / MS / DG-ID / etc.
    color_code: str = ""    # CC / NAC / RAN / CAN
    extra_info: str = ""    # RPT / Radio / D-STAR msg
    synced: bool = False
    sync_count: int = 0
    last_sync_at: float = 0.0
    last_voice_at: float = 0.0
    slot1: SlotState = field(default_factory=SlotState)
    slot2: SlotState = field(default_factory=SlotState)
    raw_lines: collections.deque[str] = field(default_factory=lambda: collections.deque(maxlen=60))
    error: str = ""


class DsdRunner:
    """Manages a single dsd-fme process using its built-in RTL-SDR backend."""

    def __init__(self, dsd_bin: str = "dsd-fme") -> None:
        self.dsd_bin = dsd_bin
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state = DsdState()
        self.on_call_event: Callable[[dict], None] | None = None
        self._last_logged_key: tuple[int, int, str, str] | None = None
        self._last_logged_at: float = 0.0

    def get_state(self) -> DsdState:
        with self._lock:
            now = time.time()
            for s in (self._state.slot1, self._state.slot2):
                if s.voice and (now - s.last_activity) > 1.8:
                    s.voice = False
                if s.active and (now - s.last_activity) > 3.8:
                    s.active = False
            s1 = SlotState(**self._state.slot1.__dict__)
            s2 = SlotState(**self._state.slot2.__dict__)
            return DsdState(
                running=self._state.running,
                recording=self._state.recording,
                freq_hz=self._state.freq_hz,
                started_at=self._state.started_at,
                protocol=self._state.protocol,
                submode=self._state.submode,
                color_code=self._state.color_code,
                extra_info=self._state.extra_info,
                synced=self._state.synced and (now - self._state.last_sync_at) < 4.0,
                sync_count=self._state.sync_count,
                last_sync_at=self._state.last_sync_at,
                last_voice_at=self._state.last_voice_at,
                slot1=s1,
                slot2=s2,
                raw_lines=collections.deque(self._state.raw_lines, maxlen=60),
                error=self._state.error,
            )

    def start(
        self,
        freq_hz: int,
        mode_flag: str = "-fs",
        dev_idx: int = 0,
        gain_db: int = 0,
        ppm: int = 0,
        bw_khz: int = 12,
        squelch: int = 0,
        vol: int = 2,
        muted: bool = False,
        record_dir: str | None = None,
    ) -> None:
        self.stop()

        rtl_spec = f"rtl:{dev_idx}:{int(freq_hz)}:{int(gain_db)}:{int(ppm)}:{int(bw_khz)}:{int(squelch)}:{int(vol)}"
        cmd = [
            self.dsd_bin,
            mode_flag,
            "-i", rtl_spec,
            "-o", "null" if muted else "pulse",
        ]
        is_rec = False
        if record_dir:
            try:
                os.makedirs(record_dir, exist_ok=True)
                # -7 <dir> MUST come before -P (per-call WAV saving)
                cmd.extend(["-7", record_dir, "-P", "-j", f"FREQ_{int(freq_hz)}"])
                is_rec = True
            except Exception:
                pass

        if os.path.exists("/usr/bin/stdbuf"):
            cmd = ["/usr/bin/stdbuf", "-oL", "-eL"] + cmd

        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("LC_ALL", "C")
        env["PULSE_LATENCY_MSEC"] = "120"

        with self._lock:
            self._state = DsdState(
                running=True,
                recording=is_rec,
                freq_hz=int(freq_hz),
                started_at=time.time(),
            )
            self._last_logged_key = None

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except Exception as e:
            with self._lock:
                self._state.running = False
                self._state.error = str(e)
            return

        self._proc = proc
        self._thread = threading.Thread(target=self._reader_loop, args=(proc,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=1.2)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=0.5)
                except Exception:
                    pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.6)
        self._thread = None
        with self._lock:
            self._state.running = False

    def _reader_loop(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, b""):
                if not raw:
                    break
                line = ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).strip()
                if not line:
                    continue
                self._parse_line(line)
        except Exception:
            pass
        finally:
            with self._lock:
                if self._proc is proc:
                    self._state.running = False

    def _parse_line(self, line: str) -> None:
        now = time.time()
        call_event: dict | None = None

        with self._lock:
            self._state.raw_lines.append(line)

            if "Failed to open rtlsdr" in line or "No supported devices found" in line:
                self._state.error = line[:64]

            m_sync = SYNC_RE.search(line)
            if m_sync:
                proto = m_sync.group(1).upper()
                self._state.protocol = proto
                self._state.synced = True
                self._state.sync_count += 1
                self._state.last_sync_at = now

            m_mode = MODE_TYPE_RE.search(line)
            if m_mode:
                txt = m_mode.group(1).upper()
                if "MS" in txt or "MONO" in txt:
                    self._state.submode = "MS"
                elif "BS" in txt:
                    self._state.submode = "BS"

            m_dg = YSF_DGID_RE.search(line)
            if m_dg:
                self._state.submode = f"DG:{m_dg.group(1)}"

            m_cc = CC_RE.search(line)
            if m_cc:
                val = m_cc.group(1).lstrip("0") or "0"
                self._state.color_code = val
                self._state.synced = True
                self._state.last_sync_at = now

            # D-STAR / YSF / M17 callsign metadata
            m_my = DSTAR_MY_RE.search(line)
            m_ur = DSTAR_UR_RE.search(line)
            m_rpt = DSTAR_RPT_RE.search(line)
            if m_rpt:
                self._state.extra_info = m_rpt.group(1).strip()[:18]

            # Active slot selection (DMR has 2 slots; YSF/D-STAR/M17/NXDN/P25 use slot1)
            slot_num = 0
            m_sb = SLOT_BRACKET_RE.search(line)
            if m_sb:
                slot_num = 1 if m_sb.group(1).lower() == "slot1" else 2
            else:
                m_sw = SLOT_WORD_RE.search(line)
                if m_sw:
                    slot_num = int(m_sw.group(1))

            target_slot = None
            if slot_num == 1:
                target_slot = self._state.slot1
            elif slot_num == 2:
                target_slot = self._state.slot2
            elif self._state.synced or m_my or m_ur:
                target_slot = self._state.slot1

            m_tg = TG_RE.search(line)
            m_src = SRC_RE.search(line)
            m_dst = DST_RE.search(line)
            m_enc = ENC_RE.search(line)
            is_act_upd = "Activity Update" in line
            is_idle = bool(IDLE_RE.search(line))
            is_voice = (not is_act_upd) and (not is_idle) and bool(VOICE_RE.search(line))
            is_term = bool(TERM_RE.search(line))

            if target_slot is not None:
                if (m_sync and not is_idle) or m_tg or m_src or m_my or is_voice:
                    target_slot.active = True
                    target_slot.last_activity = now
                if m_tg:
                    target_slot.tg = m_tg.group(1)
                if m_src:
                    target_slot.src = m_src.group(1).strip()
                elif m_my:
                    target_slot.src = m_my.group(1).strip()
                if m_dst:
                    target_slot.dst = m_dst.group(1).strip()
                elif m_ur:
                    target_slot.dst = m_ur.group(1).strip()

                # YSF callsign fallback
                if self._state.protocol == "YSF" and not m_src:
                    m_yc = YSF_CALL_RE.search(line)
                    if m_yc:
                        target_slot.src = m_yc.group(1).strip()

                if not is_act_upd:
                    if "Private" in line or "Unit to Unit" in line:
                        target_slot.call_type = "PRIV"
                    elif "Group Call" in line:
                        target_slot.call_type = "GROUP"
                    elif self._state.protocol in ("YSF", "D-STAR", "M17"):
                        target_slot.call_type = self._state.protocol

                if m_enc:
                    target_slot.encrypted = True
                    target_slot.enc_label = m_enc.group(1).upper()[:8]
                elif ("Voice LC" in line or "VLC" in line) and not m_enc:
                    target_slot.encrypted = False
                    target_slot.enc_label = ""

                if is_voice and not is_term:
                    target_slot.voice = True
                    self._state.last_voice_at = now
                elif is_term or is_idle:
                    target_slot.voice = False

                m_ta = TA_RE.search(line)
                if m_ta:
                    alias = m_ta.group(1).strip()[:22]
                    if alias:
                        target_slot.talker_alias = alias

                if (m_tg or m_src or m_my) and (target_slot.tg or target_slot.src):
                    key = (self._state.freq_hz, slot_num or 1, target_slot.tg, target_slot.src)
                    if key != self._last_logged_key or (now - self._last_logged_at) > 8.0:
                        self._last_logged_key = key
                        self._last_logged_at = now
                        call_event = {
                            "kind": "VOICE",
                            "ts": now,
                            "freq_hz": self._state.freq_hz,
                            "protocol": self._state.protocol or "DMR",
                            "cc": self._state.color_code,
                            "slot": slot_num or 1,
                            "tg": target_slot.tg,
                            "src": target_slot.src,
                            "dst": target_slot.dst,
                            "call_type": target_slot.call_type or "GROUP",
                            "encrypted": target_slot.encrypted,
                            "alias": target_slot.talker_alias,
                        }

        if call_event is not None and self.on_call_event is not None:
            try:
                self.on_call_event(call_event)
            except Exception:
                pass
