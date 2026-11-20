#!/usr/bin/env python3
"""
Zombie-free 2m/70cm RTL-SDR decoder runners for ZeroWave.
Spawns pipelines directly via subprocess.Popen (no bash -c subshells) so every
rtl_fm / pacat / multimon-ng / rtl_433 / rs41mod process is cleanly terminated,
waited on, and its USB handle is 100% released before reopening the RTL-SDR.
"""

from __future__ import annotations

import collections
import json
import os
import re
import signal
import subprocess
import threading
import time
from typing import Callable


def _terminate_and_reap(proc: subprocess.Popen | None, wait_s: float = 1.0) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=wait_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=0.5)
        else:
            proc.wait(timeout=0.1)
    except Exception:
        pass


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("LC_ALL", "C")
    env["PULSE_LATENCY_MSEC"] = "100"
    return env


def _rtl_fm_base(freq_hz: int, mode: str, s_rate: str, out_rate: int | None, gain_db: int, ppm: int, squelch: int = 0, deemp: bool = False) -> list[str]:
    cmd = ["rtl_fm", "-f", str(int(freq_hz)), "-M", mode, "-s", str(s_rate)]
    if out_rate is not None:
        cmd.extend(["-r", str(int(out_rate))])
    if int(gain_db) > 0:
        cmd.extend(["-g", str(int(gain_db))])
    if int(ppm) != 0:
        cmd.extend(["-p", str(int(ppm))])
    if int(squelch) > 0:
        cmd.extend(["-l", str(int(squelch))])
    if deemp:
        cmd.extend(["-E", "deemp"])
    cmd.extend(["-E", "dc"])
    return cmd


# ==============================================================================
# 1) ANALOG RUNNER (NFM / AM / WFM via direct rtl_fm -> pacat pipeline)
# ==============================================================================
class AnalogRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._p_rtl: subprocess.Popen | None = None
        self._p_out: subprocess.Popen | None = None
        self.running = False
        self.freq_hz = 0
        self.mod = "NFM"
        self.error = ""

    def start(
        self,
        freq_hz: int,
        mod: str = "NFM",
        gain_db: int = 0,
        ppm: int = 0,
        squelch: int = 0,
    ) -> None:
        self.stop()
        mod_u = mod.upper()
        if mod_u == "AM":
            rtl_cmd = _rtl_fm_base(freq_hz, "am", "12k", 24000, gain_db, ppm, squelch=squelch, deemp=False)
            out_rate = 24000
        elif mod_u == "WFM":
            rtl_cmd = _rtl_fm_base(freq_hz, "wbfm", "170k", 32000, gain_db, ppm, squelch=0, deemp=True)
            out_rate = 32000
        else:
            mod_u = "NFM"
            rtl_cmd = _rtl_fm_base(freq_hz, "fm", "12k", 24000, gain_db, ppm, squelch=squelch, deemp=True)
            out_rate = 24000

        pacat_cmd = [
            "pacat",
            "--format=s16le",
            f"--rate={out_rate}",
            "--channels=1",
            "--latency-msec=100",
        ]
        env = _base_env()
        p_rtl = None
        p_out = None
        try:
            p_rtl = subprocess.Popen(
                rtl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=env,
            )
            p_out = subprocess.Popen(
                pacat_cmd,
                stdin=p_rtl.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            if p_rtl.stdout:
                p_rtl.stdout.close()
            with self._lock:
                self._p_rtl = p_rtl
                self._p_out = p_out
                self.running = True
                self.freq_hz = int(freq_hz)
                self.mod = mod_u
                self.error = ""
        except Exception as e:
            _terminate_and_reap(p_rtl)
            _terminate_and_reap(p_out)
            with self._lock:
                self.running = False
                self.error = str(e)[:48]

    def stop(self) -> None:
        with self._lock:
            p_rtl, p_out = self._p_rtl, self._p_out
            self._p_rtl = None
            self._p_out = None
            self.running = False
        _terminate_and_reap(p_rtl, wait_s=1.0)
        _terminate_and_reap(p_out, wait_s=0.6)


# ==============================================================================
# 2) PAGER RUNNER (POCSAG 512/1200/2400 + FLEX via direct rtl_fm -> multimon-ng)
# ==============================================================================
POCSAG_RE = re.compile(
    r"^(POCSAG\d+|FLEX)[:|]\s*(?:Address:\s*(\d+)\s+Function:\s*(\d+)\s+(?:Alpha|Numeric|Skyper):\s*(.*)|.*\|\s*(\d+)\s*\|\s*(?:ALN|NUM)\s*\|\s*(.*))$",
    re.IGNORECASE,
)


class PagerRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._p_rtl: subprocess.Popen | None = None
        self._p_mm: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.running = False
        self.freq_hz = 439_987_500
        self.messages: collections.deque[dict] = collections.deque(maxlen=150)
        self.on_message: Callable[[dict], None] | None = None
        self.error = ""

    def start(self, freq_hz: int, gain_db: int = 0, ppm: int = 0) -> None:
        self.stop()
        g_val = int(gain_db) if int(gain_db) > 0 else 40
        rtl_cmd = _rtl_fm_base(freq_hz, "fm", "22050", None, g_val, ppm, squelch=0, deemp=False)
        mm_cmd = [
            "stdbuf", "-oL", "multimon-ng", "-t", "raw",
            "-a", "POCSAG512", "-a", "POCSAG1200", "-a", "POCSAG2400", "-a", "FLEX",
            "-f", "auto", "-e", "-u", "-",
        ]
        env = _base_env()
        p_rtl = None
        p_mm = None
        try:
            p_rtl = subprocess.Popen(
                rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=env
            )
            p_mm = subprocess.Popen(
                mm_cmd, stdin=p_rtl.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env
            )
            if p_rtl.stdout:
                p_rtl.stdout.close()
            with self._lock:
                self._p_rtl = p_rtl
                self._p_mm = p_mm
                self.running = True
                self.freq_hz = int(freq_hz)
                self.error = ""
            self._thread = threading.Thread(target=self._reader, args=(p_mm,), daemon=True)
            self._thread.start()
        except Exception as e:
            _terminate_and_reap(p_rtl)
            _terminate_and_reap(p_mm)
            with self._lock:
                self.running = False
                self.error = str(e)[:48]

    def stop(self) -> None:
        with self._lock:
            p_rtl, p_mm = self._p_rtl, self._p_mm
            self._p_rtl = None
            self._p_mm = None
            self.running = False
        _terminate_and_reap(p_rtl, wait_s=1.0)
        _terminate_and_reap(p_mm, wait_s=0.6)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    def get_messages(self) -> list[dict]:
        with self._lock:
            return list(self.messages)

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, b""):
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                m = POCSAG_RE.match(line)
                if not m:
                    continue
                proto = m.group(1).upper()
                ric = m.group(2) or m.group(5) or "?"
                text = (m.group(4) or m.group(6) or "").strip()
                text = re.sub(r"<[A-Z0-9]{2,4}>", "", text).strip()
                if not text or len(text) < 2:
                    continue
                msg = {
                    "kind": "PAGER",
                    "ts": time.time(),
                    "freq_hz": self.freq_hz,
                    "proto": proto,
                    "ric": ric,
                    "text": text[:160],
                }
                with self._lock:
                    self.messages.appendleft(msg)
                if self.on_message:
                    try:
                        self.on_message(msg)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            with self._lock:
                if self._p_mm is proc:
                    self.running = False


# ==============================================================================
# 3) APRS RUNNER (AFSK1200 via direct rtl_fm -> multimon-ng)
# ==============================================================================
APRS_RE = re.compile(r"^APRS:\s*([A-Z0-9-]+)>([^:]+):(.*)$", re.IGNORECASE)
APRS_POS_RE = re.compile(r"(\d{4}\.\d{2}[NS]).(\d{5}\.\d{2}[EW])")


class AprsRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._p_rtl: subprocess.Popen | None = None
        self._p_mm: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.running = False
        self.freq_hz = 144_800_000
        self.packets: collections.deque[dict] = collections.deque(maxlen=150)
        self.on_packet: Callable[[dict], None] | None = None
        self.error = ""

    def start(self, freq_hz: int, gain_db: int = 0, ppm: int = 0) -> None:
        self.stop()
        g_val = int(gain_db) if int(gain_db) > 0 else 40
        rtl_cmd = _rtl_fm_base(freq_hz, "fm", "22050", None, g_val, ppm, squelch=0, deemp=False)
        mm_cmd = ["stdbuf", "-oL", "multimon-ng", "-t", "raw", "-a", "AFSK1200", "-A", "-"]
        env = _base_env()
        p_rtl = None
        p_mm = None
        try:
            p_rtl = subprocess.Popen(
                rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=env
            )
            p_mm = subprocess.Popen(
                mm_cmd, stdin=p_rtl.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env
            )
            if p_rtl.stdout:
                p_rtl.stdout.close()
            with self._lock:
                self._p_rtl = p_rtl
                self._p_mm = p_mm
                self.running = True
                self.freq_hz = int(freq_hz)
                self.error = ""
            self._thread = threading.Thread(target=self._reader, args=(p_mm,), daemon=True)
            self._thread.start()
        except Exception as e:
            _terminate_and_reap(p_rtl)
            _terminate_and_reap(p_mm)
            with self._lock:
                self.running = False
                self.error = str(e)[:48]

    def stop(self) -> None:
        with self._lock:
            p_rtl, p_mm = self._p_rtl, self._p_mm
            self._p_rtl = None
            self._p_mm = None
            self.running = False
        _terminate_and_reap(p_rtl, wait_s=1.0)
        _terminate_and_reap(p_mm, wait_s=0.6)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    def get_packets(self) -> list[dict]:
        with self._lock:
            return list(self.packets)

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, b""):
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                m = APRS_RE.match(line)
                if not m:
                    continue
                src, path, payload = m.group(1), m.group(2), m.group(3).strip()
                pos = ""
                mp = APRS_POS_RE.search(payload)
                if mp:
                    pos = f"{mp.group(1)} {mp.group(2)}"
                pkt = {
                    "kind": "APRS",
                    "ts": time.time(),
                    "freq_hz": self.freq_hz,
                    "src": src,
                    "path": path[:20],
                    "pos": pos,
                    "text": payload[:120],
                }
                with self._lock:
                    self.packets.appendleft(pkt)
                if self.on_packet:
                    try:
                        self.on_packet(pkt)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            with self._lock:
                if self._p_mm is proc:
                    self.running = False


# ==============================================================================
# 4) ISM 433 MHz SENSOR RUNNER (rtl_433 JSON)
# ==============================================================================
class Ism433Runner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.running = False
        self.freq_hz = 433_920_000
        self.sensors: dict[str, dict] = {}
        self.total_rx = 0
        self.on_sensor: Callable[[dict], None] | None = None
        self.error = ""

    def start(self, freq_hz: int = 433_920_000, gain_db: int = 0, ppm: int = 0) -> None:
        self.stop()
        cmd = ["rtl_433", "-f", str(int(freq_hz)), "-F", "json", "-M", "level"]
        if int(gain_db) > 0:
            cmd.extend(["-g", str(int(gain_db))])
        if int(ppm) != 0:
            cmd.extend(["-p", str(int(ppm))])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=_base_env(),
            )
            with self._lock:
                self._proc = proc
                self.running = True
                self.freq_hz = int(freq_hz)
                self.error = ""
            self._thread = threading.Thread(target=self._reader, args=(proc,), daemon=True)
            self._thread.start()
        except Exception as e:
            with self._lock:
                self.running = False
                self.error = str(e)[:48]

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            self.running = False
        _terminate_and_reap(proc, wait_s=1.0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    def get_sensors(self) -> list[dict]:
        with self._lock:
            items = list(self.sensors.values())
        items.sort(key=lambda x: x.get("ts", 0), reverse=True)
        return items

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, b""):
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                model = str(d.get("model", "Unknown"))[:22]
                sid = str(d.get("id", d.get("channel", "-")))
                key = f"{model}|{sid}"
                parts = []
                if "temperature_C" in d:
                    parts.append(f"{float(d['temperature_C']):.1f}C")
                elif "temperature_F" in d:
                    c = (float(d["temperature_F"]) - 32.0) * 5.0 / 9.0
                    parts.append(f"{c:.1f}C")
                if "humidity" in d:
                    parts.append(f"{int(round(float(d['humidity'])))}%")
                if "pressure_kPa" in d:
                    parts.append(f"{float(d['pressure_kPa']):.0f}kPa")
                elif "pressure_PSI" in d:
                    parts.append(f"{float(d['pressure_PSI']):.1f}PSI")
                if "wind_avg_km_h" in d:
                    parts.append(f"{float(d['wind_avg_km_h']):.1f}km/h")
                if "battery_ok" in d:
                    parts.append("BAT:OK" if d["battery_ok"] else "BAT:LOW")
                if not parts:
                    for k in ("cmd", "state", "code", "button"):
                        if k in d:
                            parts.append(f"{k}:{d[k]}")
                entry = {
                    "kind": "ISM433",
                    "ts": time.time(),
                    "freq_hz": self.freq_hz,
                    "model": model,
                    "id": sid,
                    "summary": " ".join(parts) if parts else "RX signal",
                    "rssi": d.get("rssi", None),
                    "snr": d.get("snr", None),
                }
                with self._lock:
                    prev = self.sensors.get(key)
                    entry["hits"] = (prev.get("hits", 0) + 1) if prev else 1
                    self.sensors[key] = entry
                    self.total_rx += 1
                if self.on_sensor:
                    try:
                        self.on_sensor(entry)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            with self._lock:
                if self._proc is proc:
                    self.running = False


# ==============================================================================
# 5) RADIOSONDE RUNNER (Vaisala RS41 via direct rtl_fm -> sox -> rs41mod)
# ==============================================================================
RS41_LINE_RE = re.compile(
    r"\[(\d+)\]\s+\(([A-Z0-9_-]+)\).*?lat:\s*([-0-9.]+)\s+lon:\s*([-0-9.]+)\s+alt:\s*([-0-9.]+)",
    re.IGNORECASE,
)
RS41_VEL_RE = re.compile(r"vH:\s*([-0-9.]+).*?vV:\s*([-0-9.]+)", re.IGNORECASE)
RS41_TEMP_RE = re.compile(r"T=([-0-9.]+)C", re.IGNORECASE)


class SondeRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: list[subprocess.Popen] = []
        self._thread: threading.Thread | None = None
        self.running = False
        self.freq_hz = 403_000_000
        self.latest: dict | None = None
        self.raw_line = ""
        self.error = ""
        self.on_sonde: Callable[[dict], None] | None = None

    def start(self, freq_hz: int, gain_db: int = 0, ppm: int = 0) -> None:
        self.stop()
        if not os.path.exists("/usr/local/bin/rs41mod"):
            with self._lock:
                self.error = "rs41mod not installed"
            return
        g_val = int(gain_db) if int(gain_db) > 0 else 40
        rtl_cmd = _rtl_fm_base(freq_hz, "fm", "48000", None, g_val, ppm, squelch=0, deemp=False)
        sox_cmd = ["sox", "-t", "raw", "-r", "48000", "-e", "signed", "-b", "16", "-c", "1", "-", "-t", "wav", "-"]
        rs_cmd = ["stdbuf", "-oL", "/usr/local/bin/rs41mod", "--ptu", "-"]
        env = _base_env()
        p1 = p2 = p3 = None
        try:
            p1 = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=env)
            p2 = subprocess.Popen(sox_cmd, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
            p3 = subprocess.Popen(rs_cmd, stdin=p2.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
            if p1.stdout:
                p1.stdout.close()
            if p2.stdout:
                p2.stdout.close()
            with self._lock:
                self._procs = [p1, p2, p3]
                self.running = True
                self.freq_hz = int(freq_hz)
                self.latest = None
                self.error = ""
            self._thread = threading.Thread(target=self._reader, args=(p3,), daemon=True)
            self._thread.start()
        except Exception as e:
            for p in (p1, p2, p3):
                _terminate_and_reap(p)
            with self._lock:
                self.running = False
                self.error = str(e)[:48]

    def stop(self) -> None:
        with self._lock:
            procs = list(self._procs)
            self._procs = []
            self.running = False
        for p in procs:
            _terminate_and_reap(p, wait_s=0.8)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    def get_latest(self) -> tuple[dict | None, str]:
        with self._lock:
            return (dict(self.latest) if self.latest else None, self.raw_line)

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, b""):
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                with self._lock:
                    self.raw_line = line[:70]
                m = RS41_LINE_RE.search(line)
                if m:
                    mv = RS41_VEL_RE.search(line)
                    mt = RS41_TEMP_RE.search(line)
                    rec = {
                        "kind": "SONDE",
                        "ts": time.time(),
                        "freq_hz": self.freq_hz,
                        "frame": m.group(1),
                        "sonde_id": m.group(2),
                        "lat": m.group(3),
                        "lon": m.group(4),
                        "alt_m": m.group(5),
                        "vh": mv.group(1) if mv else "",
                        "vv": mv.group(2) if mv else "",
                        "temp_c": mt.group(1) if mt else "",
                    }
                    with self._lock:
                        self.latest = rec
                    if self.on_sonde:
                        try:
                            self.on_sonde(rec)
                        except Exception:
                            pass
        except Exception:
            pass
        finally:
            with self._lock:
                if proc in self._procs:
                    self.running = False
