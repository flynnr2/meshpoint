"""Opt-in single-modem SX1301 source for the existing Meshpoint pipeline."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from src.capture.base import CaptureSource
from src.hal.sx1301_wrapper import SX1301Wrapper
from src.models.packet import Protocol, RawCapture
from src.models.signal import SignalMetrics

logger = logging.getLogger(__name__)


class SX1301CaptureSource(CaptureSource):
    def __init__(self, config):
        self._radio = config.radio
        self._transmit = config.transmit.enabled
        cap = config.capture
        self._wrapper = SX1301Wrapper(cap.sx1301_lib_path, cap.sx1301_reset_gpio,
                                    cap.sx1301_clksrc, cap.sx1301_tx_gain_lut)
        self._running = False
        self._restart_lock = asyncio.Lock()
        self._raw_observers = []

    def on_raw_capture(self, callback):
        """Observe every CRC-valid native frame before decoder filtering."""
        self._raw_observers.append(callback)

    @property
    def name(self):
        return "concentrator"

    @property
    def is_running(self):
        return self._running

    @property
    def rx_crc_stats(self):
        return self._wrapper.crc_bad_count, self._wrapper.no_crc_count

    async def start(self):
        def initialize():
            self._wrapper.load()
            self._wrapper.reset()
            self._wrapper.configure(self._radio, self._transmit)
            self._wrapper.start()
        await asyncio.to_thread(initialize)
        self._running = True
        logger.info("SX1301 started: single SF%s / %skHz at %s MHz; TX=%s",
                    self._radio.spreading_factor, self._radio.bandwidth_khz,
                    self._radio.frequency_mhz, self._transmit)

    async def stop(self):
        self._running = False
        await asyncio.to_thread(self._wrapper.stop)

    async def restart_pipeline(self):
        async with self._restart_lock:
            await self.stop()
            await self.start()

    async def packets(self):
        while self._running:
            try:
                packets = await asyncio.to_thread(self._wrapper.receive)
            except Exception:
                self._running = False
                await asyncio.to_thread(self._wrapper.stop)
                raise
            for p in packets:
                observed = datetime.now(timezone.utc)
                raw = RawCapture(payload=p["payload"],
                    signal=SignalMetrics(rssi=p["rssi"], snr=p["snr"],
                        frequency_mhz=p["frequency_hz"] / 1e6,
                        spreading_factor=p["spreading_factor"],
                        bandwidth_khz=p["bandwidth_khz"], coding_rate=f"4/{p['coderate'] + 4}",
                        timestamp=observed), capture_source=self.name,
                    timestamp=observed, protocol_hint=Protocol.MESHTASTIC)
                for callback in self._raw_observers:
                    try:
                        callback(raw)
                    except Exception:
                        logger.exception('SX1301 raw observer failed')
                yield raw
            await asyncio.sleep(0.01)
