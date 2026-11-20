#!/usr/bin/env python3
"""
Fast RTL-SDR wideband spectrum sweeper & channel-list RSSI pre-checker
using ctypes -> librtlsdr + NumPy FFT (max-hold across TDMA burst duration).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import math
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass
class PeakCandidate:
    freq_hz: int
    power_db: float
    snr_db: float
    bw_hz: int


class RtlSdrError(RuntimeError):
    pass


def _load_librtlsdr() -> ctypes.CDLL:
    candidates = [
        ctypes.util.find_library("rtlsdr"),
        "librtlsdr.so.2",
        "librtlsdr.so.0",
        "librtlsdr.so",
    ]
    last_err = None
    for name in candidates:
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
            lib.rtlsdr_get_device_count.restype = ctypes.c_uint32
            lib.rtlsdr_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32]
            lib.rtlsdr_open.restype = ctypes.c_int
            lib.rtlsdr_close.argtypes = [ctypes.c_void_p]
            lib.rtlsdr_close.restype = ctypes.c_int
            lib.rtlsdr_set_sample_rate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            lib.rtlsdr_set_sample_rate.restype = ctypes.c_int
            lib.rtlsdr_set_center_freq.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            lib.rtlsdr_set_center_freq.restype = ctypes.c_int
            lib.rtlsdr_set_tuner_gain_mode.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.rtlsdr_set_tuner_gain_mode.restype = ctypes.c_int
            lib.rtlsdr_set_tuner_gain.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.rtlsdr_set_tuner_gain.restype = ctypes.c_int
            lib.rtlsdr_set_agc_mode.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.rtlsdr_set_agc_mode.restype = ctypes.c_int
            lib.rtlsdr_set_freq_correction.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.rtlsdr_set_freq_correction.restype = ctypes.c_int
            lib.rtlsdr_reset_buffer.argtypes = [ctypes.c_void_p]
            lib.rtlsdr_reset_buffer.restype = ctypes.c_int
            lib.rtlsdr_read_sync.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ]
            lib.rtlsdr_read_sync.restype = ctypes.c_int
            return lib
        except OSError as e:
            last_err = e
    raise RtlSdrError(f"Could not load librtlsdr: {last_err}")


class RtlSdrSweeper:
    """
    Lightweight synchronous RTL-SDR spectrum scanner.
    Always call close() before spawning dsd-fme (exclusive USB access).
    """

    SAMPLE_RATE = 2_400_000
    USABLE_SPAN = 1_800_000  # Trim outer 12.5% roll-off on each side
    FFT_SIZE = 2048          # 1171.875 Hz/bin -> 12.5 kHz DMR spans ~10-11 bins

    def __init__(self, device_index: int = 0) -> None:
        self.device_index = device_index
        self._lib: ctypes.CDLL | None = None
        self._dev = ctypes.c_void_p(0)
        self._is_open = False
        self._window = np.hanning(self.FFT_SIZE).astype(np.float32)
        self._win_norm = float(np.sum(self._window ** 2))
        self._gain_db: float | None = 36.4  # None = auto
        self._ppm: int = 0

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self, gain_db: float | None = 36.4, ppm: int = 0) -> None:
        if self._is_open:
            self.configure(gain_db=gain_db, ppm=ppm)
            return
        if self._lib is None:
            self._lib = _load_librtlsdr()
        count = self._lib.rtlsdr_get_device_count()
        if count == 0:
            raise RtlSdrError("No RTL-SDR device found")
        dev = ctypes.c_void_p(0)
        rc = self._lib.rtlsdr_open(ctypes.byref(dev), self.device_index)
        if rc != 0 or not dev.value:
            raise RtlSdrError(f"rtlsdr_open failed (rc={rc})")
        self._dev = dev
        self._is_open = True
        try:
            self._lib.rtlsdr_set_sample_rate(self._dev, self.SAMPLE_RATE)
            self._lib.rtlsdr_set_agc_mode(self._dev, 0)
            self.configure(gain_db=gain_db, ppm=ppm)
        except Exception:
            self.close()
            raise

    def configure(self, gain_db: float | None = None, ppm: int = 0) -> None:
        self._gain_db = gain_db
        self._ppm = ppm
        if not self._is_open or self._lib is None:
            return
        if gain_db is None:
            self._lib.rtlsdr_set_tuner_gain_mode(self._dev, 0)
        else:
            self._lib.rtlsdr_set_tuner_gain_mode(self._dev, 1)
            self._lib.rtlsdr_set_tuner_gain(self._dev, int(round(gain_db * 10)))
        if ppm != 0:
            self._lib.rtlsdr_set_freq_correction(self._dev, int(ppm))

    def close(self) -> None:
        if self._is_open and self._lib is not None and self._dev.value:
            try:
                self._lib.rtlsdr_close(self._dev)
            except Exception:
                pass
        self._dev = ctypes.c_void_p(0)
        self._is_open = False

    def capture_psd(self, center_hz: int, burst_ms: int = 68) -> tuple[np.ndarray, np.ndarray, float]:
        """
        Tune to center_hz, capture ~burst_ms of IQ (covers full 60ms DMR TDMA frame),
        compute max-hold PSD across sub-windows so 30ms DMR simplex bursts aren't missed.

        Returns (freqs_hz, psd_db, noise_floor_db) trimmed to the usable central span.
        """
        if not self._is_open or self._lib is None:
            raise RtlSdrError("RTL-SDR is not open")

        self._lib.rtlsdr_set_center_freq(self._dev, int(center_hz))
        self._lib.rtlsdr_reset_buffer(self._dev)

        # Discard ~4 ms settling samples
        settle_bytes = 16384
        n_read = ctypes.c_int(0)
        settle_buf = (ctypes.c_ubyte * settle_bytes)()
        self._lib.rtlsdr_read_sync(self._dev, settle_buf, settle_bytes, ctypes.byref(n_read))

        # Capture enough samples to cover >= 60ms (full DMR TDMA superframe = 60ms)
        n_blocks = max(8, int(round((burst_ms / 1000.0) * self.SAMPLE_RATE / self.FFT_SIZE)))
        # Ensure multiple of 4 for 4 sub-frames (~17ms each)
        n_blocks = ((n_blocks + 3) // 4) * 4
        n_samples = n_blocks * self.FFT_SIZE
        n_bytes = n_samples * 2
        # Round up to 16384-byte USB transfer alignment
        alloc_bytes = ((n_bytes + 16383) // 16384) * 16384
        buf = (ctypes.c_ubyte * alloc_bytes)()

        rc = self._lib.rtlsdr_read_sync(self._dev, buf, alloc_bytes, ctypes.byref(n_read))
        if rc != 0 or n_read.value < n_bytes:
            raise RtlSdrError(f"rtlsdr_read_sync failed (rc={rc}, got={n_read.value})")

        raw = np.frombuffer(buf, dtype=np.uint8, count=n_bytes).astype(np.float32)
        raw -= 127.4
        iq = raw[0::2] + 1j * raw[1::2]
        iq -= np.mean(iq)  # Remove DC offset
        iq = iq.reshape(n_blocks, self.FFT_SIZE) * self._window

        spec = np.fft.fftshift(np.fft.fft(iq, axis=1), axes=1)
        pwr = (spec.real ** 2 + spec.imag ** 2) / (self._win_norm * self.FFT_SIZE)

        # Split into 4 time slices (~17ms each), average within slice, then max-hold across slices
        sub = pwr.reshape(4, n_blocks // 4, self.FFT_SIZE).mean(axis=1)
        psd = np.max(sub, axis=0)
        psd_db = 10.0 * np.log10(np.maximum(psd, 1e-12))

        freqs = np.fft.fftshift(np.fft.fftfreq(self.FFT_SIZE, d=1.0 / self.SAMPLE_RATE)) + center_hz

        # Trim outer roll-off edges
        half_usable = self.USABLE_SPAN / 2.0
        mask = (freqs >= center_hz - half_usable) & (freqs <= center_hz + half_usable)
        freqs = freqs[mask]
        psd_db = psd_db[mask]

        # Robust noise floor (35th percentile ignores active signals)
        noise_floor = float(np.percentile(psd_db, 35))
        return freqs, psd_db, noise_floor

    def find_peaks_in_window(
        self,
        freqs: np.ndarray,
        psd_db: np.ndarray,
        noise_floor: float,
        start_hz: int,
        end_hz: int,
        step_hz: int = 12_500,
        threshold_db: float = 11.0,
        min_bw_hz: int = 3_500,
        max_bw_hz: int = 28_000,
        ignore_fn: Callable[[int], bool] | None = None,
    ) -> list[PeakCandidate]:
        """
        Extract narrowband DMR-like peak candidates from a single FFT window.
        Rejects ultra-narrow spurs (< min_bw_hz) and wideband carriers (> max_bw_hz).
        """
        bin_hz = self.SAMPLE_RATE / self.FFT_SIZE
        # Smooth slightly with 3-bin moving average (~3.5 kHz) for clean peak picking
        kernel = np.array([0.25, 0.5, 0.25], dtype=np.float32)
        smooth = np.convolve(psd_db, kernel, mode="same")

        above = smooth > (noise_floor + threshold_db)
        if not np.any(above):
            return []

        peaks: list[PeakCandidate] = []
        # Find contiguous islands above (noise_floor + threshold_db * 0.55)
        edge_thresh = noise_floor + max(5.0, threshold_db * 0.55)
        in_island = smooth > edge_thresh

        n = len(smooth)
        i = 0
        while i < n:
            if not in_island[i]:
                i += 1
                continue
            j = i
            while j < n and in_island[j]:
                j += 1
            seg = smooth[i:j]
            max_rel = int(np.argmax(seg))
            peak_val = float(seg[max_rel])
            snr = peak_val - noise_floor
            if snr >= threshold_db:
                # Measure occupied bandwidth relative to peak (-12 dB down, clamped above noise floor)
                # so very strong (+60 dB) DMR signals aren't rejected due to spectral skirt width!
                pk_idx = i + max_rel
                bw_thresh = max(noise_floor + 4.0, peak_val - 12.0)
                l_idx = pk_idx
                while l_idx > i and smooth[l_idx - 1] >= bw_thresh:
                    l_idx -= 1
                r_idx = pk_idx
                while r_idx < j - 1 and smooth[r_idx + 1] >= bw_thresh:
                    r_idx += 1
                bw = int(round((r_idx - l_idx + 1) * bin_hz))

                # Weighted centroid around peak (-10 dB top) for accurate frequency estimate
                lo = max(i, pk_idx - 4)
                hi = min(j, pk_idx + 5)
                w = np.maximum(smooth[lo:hi] - (peak_val - 15.0), 0.1)
                est_freq = float(np.sum(freqs[lo:hi] * w) / np.sum(w))

                # Snap to channel raster
                snapped = int(round(est_freq / step_hz) * step_hz)
                if start_hz <= snapped <= end_hz and min_bw_hz <= bw <= max_bw_hz:
                    if ignore_fn is None or not ignore_fn(snapped):
                        peaks.append(PeakCandidate(
                            freq_hz=snapped,
                            power_db=peak_val,
                            snr_db=snr,
                            bw_hz=bw,
                        ))
            i = j

        # Deduplicate same snapped channel (keep strongest)
        best: dict[int, PeakCandidate] = {}
        for p in peaks:
            prev = best.get(p.freq_hz)
            if prev is None or p.snr_db > prev.snr_db:
                best[p.freq_hz] = p

        out = list(best.values())
        out.sort(key=lambda p: p.snr_db, reverse=True)
        return out

    def measure_channels_fast(
        self,
        freqs_hz: list[int],
        threshold_db: float = 10.0,
        on_window: Callable[[int, np.ndarray, np.ndarray, float], None] | None = None,
    ) -> list[PeakCandidate]:
        """
        Fast cluster-based RSSI pre-check for a list of known frequencies.
        Groups channels within USABLE_SPAN so dozens of channels are checked in <0.3s!
        """
        if not freqs_hz:
            return []
        sorted_f = sorted(set(int(f) for f in freqs_hz))
        clusters: list[list[int]] = []
        cur: list[int] = [sorted_f[0]]
        for f in sorted_f[1:]:
            if f - cur[0] <= self.USABLE_SPAN - 100_000:
                cur.append(f)
            else:
                clusters.append(cur)
                cur = [f]
        clusters.append(cur)

        hits: list[PeakCandidate] = []
        for cl in clusters:
            center = (cl[0] + cl[-1]) // 2
            freqs, psd_db, noise_floor = self.capture_psd(center)
            if on_window is not None:
                on_window(center, freqs, psd_db, noise_floor)
            for ch in cl:
                # Measure peak power within +/- 6.25 kHz of target channel
                mask = (freqs >= ch - 6_500) & (freqs <= ch + 6_500)
                if not np.any(mask):
                    continue
                ch_pwr = float(np.max(psd_db[mask]))
                snr = ch_pwr - noise_floor
                if snr >= threshold_db:
                    hits.append(PeakCandidate(
                        freq_hz=ch,
                        power_db=ch_pwr,
                        snr_db=snr,
                        bw_hz=12_500,
                    ))

        hits.sort(key=lambda p: p.snr_db, reverse=True)
        return hits


def plan_band_centers(start_hz: int, end_hz: int, span_hz: int = RtlSdrSweeper.USABLE_SPAN) -> list[int]:
    """Compute center frequencies to cover [start_hz, end_hz] with small overlap."""
    if end_hz <= start_hz:
        return [start_hz]
    total = end_hz - start_hz
    if total <= span_hz:
        return [(start_hz + end_hz) // 2]
    step = int(span_hz * 0.92)
    centers = []
    c = start_hz + span_hz // 2
    while c - span_hz // 2 < end_hz:
        centers.append(int(c))
        c += step
    return centers
