#!/usr/bin/env python3
"""
Offline SQLite Callsign (RadioID.net) & Talkgroup (BrandMeister) database
with background downloader and progress tracking for ZeroWave.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
import time
import urllib.request
from typing import Callable

RADIOID_CSV_URL = "https://radioid.net/static/user.csv"
BM_TG_URL = "https://api.brandmeister.network/v2/talkgroup"


class RadioDatabase:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._cache_id: dict[int, dict | None] = {}
        self._cache_tg: dict[int, str] = {}

        # Download status
        self.downloading = False
        self.dl_stage = ""
        self.dl_pct = 0.0
        self.dl_error = ""
        self.user_count = 0
        self.tg_count = 0
        self.updated_at = ""

        self._open_existing()

    def _open_existing(self) -> None:
        with self._lock:
            if not os.path.exists(self.db_path):
                return
            try:
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM users")
                self.user_count = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM talkgroups")
                self.tg_count = int(cur.fetchone()[0])
                try:
                    cur.execute("SELECT value FROM meta WHERE key='updated_at'")
                    row = cur.fetchone()
                    if row:
                        self.updated_at = str(row[0])
                except Exception:
                    pass
                self._conn = conn
            except Exception:
                self._conn = None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def lookup_id(self, radio_id: str | int) -> dict | None:
        try:
            rid = int(str(radio_id).strip())
        except Exception:
            return None
        with self._lock:
            if rid in self._cache_id:
                return self._cache_id[rid]
            if self._conn is None:
                return None
            try:
                cur = self._conn.cursor()
                cur.execute(
                    "SELECT callsign, first_name, last_name, city, state, country FROM users WHERE radio_id = ?",
                    (rid,),
                )
                row = cur.fetchone()
                if row:
                    res = {
                        "callsign": row[0] or "",
                        "first_name": row[1] or "",
                        "last_name": row[2] or "",
                        "city": row[3] or "",
                        "state": row[4] or "",
                        "country": row[5] or "",
                    }
                else:
                    res = None
                if len(self._cache_id) > 1024:
                    self._cache_id.clear()
                self._cache_id[rid] = res
                return res
            except Exception:
                return None

    def lookup_tg(self, tg_id: str | int) -> str:
        try:
            tid = int(str(tg_id).strip())
        except Exception:
            return ""
        with self._lock:
            if tid in self._cache_tg:
                return self._cache_tg[tid]
            if self._conn is None:
                return ""
            try:
                cur = self._conn.cursor()
                cur.execute("SELECT name FROM talkgroups WHERE tg_id = ?", (tid,))
                row = cur.fetchone()
                name = str(row[0]) if row and row[0] else ""
                if len(self._cache_tg) > 512:
                    self._cache_tg.clear()
                self._cache_tg[tid] = name
                return name
            except Exception:
                return ""

    def start_download(self, on_done: Callable[[bool, str], None] | None = None) -> bool:
        with self._lock:
            if self.downloading:
                return False
            self.downloading = True
            self.dl_stage = "Connecting..."
            self.dl_pct = 0.0
            self.dl_error = ""

        threading.Thread(target=self._download_worker, args=(on_done,), daemon=True).start()
        return True

    def _download_worker(self, on_done: Callable[[bool, str], None] | None) -> None:
        tmp_db = self.db_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            if os.path.exists(tmp_db):
                os.remove(tmp_db)

            conn = sqlite3.connect(tmp_db)
            cur = conn.cursor()
            cur.execute("PRAGMA synchronous = OFF")
            cur.execute("PRAGMA journal_mode = MEMORY")
            cur.execute("""
                CREATE TABLE users (
                    radio_id INTEGER PRIMARY KEY,
                    callsign TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    city TEXT,
                    state TEXT,
                    country TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE talkgroups (
                    tg_id INTEGER PRIMARY KEY,
                    name TEXT
                )
            """)
            cur.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

            # 1) Download & stream-parse RadioID.net user.csv (~16.5 MB)
            with self._lock:
                self.dl_stage = "Downloading RadioID.net..."
                self.dl_pct = 0.02

            req = urllib.request.Request(RADIOID_CSV_URL, headers={"User-Agent": "ZeroWave/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total_len = int(resp.headers.get("Content-Length") or 17_500_000)
                downloaded = 0
                buf = bytearray()
                header_parsed = False
                batch: list[tuple] = []
                n_users = 0

                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    buf.extend(chunk)
                    nl = buf.rfind(b"\n")
                    if nl == -1:
                        continue
                    block = bytes(buf[:nl]).decode("utf-8", errors="ignore")
                    del buf[: nl + 1]
                    reader = csv.reader(io.StringIO(block))
                    for row in reader:
                        if not row:
                            continue
                        if not header_parsed:
                            header_parsed = True
                            if row[0].strip().upper() == "RADIO_ID":
                                continue
                        try:
                            rid = int(row[0].strip())
                        except Exception:
                            continue
                        call = row[1].strip() if len(row) > 1 else ""
                        fname = row[2].strip() if len(row) > 2 else ""
                        lname = row[3].strip() if len(row) > 3 else ""
                        city = row[4].strip() if len(row) > 4 else ""
                        state = row[5].strip() if len(row) > 5 else ""
                        country = row[6].strip() if len(row) > 6 else ""
                        batch.append((rid, call, fname, lname, city, state, country))
                        if len(batch) >= 5000:
                            cur.executemany(
                                "INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?)",
                                batch,
                            )
                            n_users += len(batch)
                            batch.clear()
                    with self._lock:
                        self.dl_pct = min(0.85, 0.05 + 0.80 * (downloaded / max(1, total_len)))
                        self.dl_stage = f"RadioID: {n_users // 1000}k IDs..."

                if batch:
                    cur.executemany(
                        "INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?)",
                        batch,
                    )
                    n_users += len(batch)
                    batch.clear()

            conn.commit()

            # 2) Download BrandMeister Talkgroups JSON
            with self._lock:
                self.dl_stage = "Downloading Talkgroups..."
                self.dl_pct = 0.88

            n_tgs = 0
            try:
                req_tg = urllib.request.Request(BM_TG_URL, headers={"User-Agent": "ZeroWave/1.0"})
                with urllib.request.urlopen(req_tg, timeout=20) as resp:
                    tg_data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                if isinstance(tg_data, dict):
                    tg_batch = []
                    for k, v in tg_data.items():
                        try:
                            tid = int(k)
                            name = str(v).strip()
                            if name:
                                tg_batch.append((tid, name))
                        except Exception:
                            continue
                    if tg_batch:
                        cur.executemany("INSERT OR REPLACE INTO talkgroups VALUES (?,?)", tg_batch)
                        n_tgs = len(tg_batch)
            except Exception:
                # Non-fatal if BM API is unreachable; seed key common TGs
                fallback_tgs = [
                    (9, "Local"), (91, "Worldwide"), (92, "Europe"), (93, "North America"),
                    (262, "Germany"), (2620, "DL North"), (2621, "Berlin/Brandenburg"),
                    (2622, "Hamburg/SH"), (2623, "Niedersachsen"), (2624, "NRW"),
                    (2625, "Rheinland-Pfalz"), (2626, "Hessen"), (2627, "Baden-Wuertt."),
                    (2628, "Bayern"), (2629, "Sachsen/Thueringen"), (9990, "Parrot"),
                ]
                cur.executemany("INSERT OR REPLACE INTO talkgroups VALUES (?,?)", fallback_tgs)
                n_tgs = len(fallback_tgs)

            stamp = time.strftime("%Y-%m-%d %H:%M")
            cur.execute("INSERT OR REPLACE INTO meta VALUES ('updated_at', ?)", (stamp,))
            conn.commit()
            conn.close()

            # Atomically swap DB
            with self._lock:
                if self._conn is not None:
                    try:
                        self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
                os.replace(tmp_db, self.db_path)
                self._cache_id.clear()
                self._cache_tg.clear()
                self.dl_pct = 1.0
                self.dl_stage = "Done!"
                self.downloading = False
            self._open_existing()

            msg = f"DB OK: {n_users // 1000}k IDs, {n_tgs} TGs"
            if on_done:
                on_done(True, msg)
        except Exception as e:
            try:
                if os.path.exists(tmp_db):
                    os.remove(tmp_db)
            except Exception:
                pass
            with self._lock:
                self.downloading = False
                self.dl_error = str(e)[:44]
                self.dl_stage = f"Err: {self.dl_error}"
            if on_done:
                on_done(False, f"DB Err: {str(e)[:32]}")
