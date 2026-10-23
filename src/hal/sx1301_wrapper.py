"""SX1301-only native shim. No SX1302 structs or register enums cross this ABI."""
from __future__ import annotations

import ctypes as c
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SX1301TxPacket:
    payload: bytes
    freq_hz: int
    datarate: int
    bandwidth: int
    coderate: int
    rf_power: int
    preamble: int = 16
    no_crc: bool = False
    no_header: bool = False
    invert_pol: bool = False

    @property
    def size(self):
        return len(self.payload)


class SX1301Wrapper:
    """Serialize RX/TX and cleanup against a private, matching-header C shim."""

    spectral_scan_supported = False
    hardware_backend = "sx1301"

    def __init__(self, lib_path, reset_gpio=25, clksrc=1, tx_gain_lut=None):
        self._path = Path(lib_path).resolve()
        if not 0 <= reset_gpio <= 27 or clksrc not in (0, 1):
            raise ValueError("Invalid SX1301 reset GPIO or clock source")
        self._gpio, self._clksrc = reset_gpio, clksrc
        self._gains = tx_gain_lut or []
        self._lib = None
        self._lock = threading.RLock()
        self._started = False
        self._radio = None
        self._tx = False

    def load(self):
        if self._lib is not None:
            return
        lib = c.CDLL(str(self._path))
        functions = {
            "mp1301_abi_version": ([], c.c_int),
            "mp1301_configure": ([c.c_uint32] + [c.c_int] * 4, c.c_int),
            "mp1301_add_gain": ([c.c_int] * 5, c.c_int),
            "mp1301_start": ([], c.c_int), "mp1301_stop": ([], c.c_int),
            "mp1301_receive": ([], c.c_int),
            "mp1301_packet": ([c.c_uint, c.POINTER(c.c_ubyte), c.c_uint,
                c.POINTER(c.c_uint32), c.POINTER(c.c_uint32),
                c.POINTER(c.c_int), c.POINTER(c.c_int),
                c.POINTER(c.c_float), c.POINTER(c.c_float), c.POINTER(c.c_int)], c.c_int),
            "mp1301_send": ([c.POINTER(c.c_ubyte), c.c_uint, c.c_int, c.c_int], c.c_int),
            "mp1301_airtime": ([c.POINTER(c.c_ubyte), c.c_uint, c.c_int, c.c_int], c.c_int),
            "mp1301_tx_status": ([], c.c_int),
            "mp1301_last_tx_observed": ([], c.c_int),
            "mp1301_crc_bad": ([], c.c_uint), "mp1301_no_crc": ([], c.c_uint),
        }
        for name, (args, result) in functions.items():
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, result
        if lib.mp1301_abi_version() != 1:
            raise RuntimeError("Incompatible SX1301 native shim ABI")
        # Additive diagnostic export: old ABI-1 libraries remain usable for RX.
        diagnostics = getattr(lib, "mp1301_channel_diagnostics", None)
        if diagnostics is not None:
            diagnostics.argtypes = [c.POINTER(c.c_int32), c.c_uint]
            diagnostics.restype = c.c_int
        self._lib = lib

    def reset(self):
        with self._lock:
            if self._started:
                raise RuntimeError("Stop SX1301 before GPIO reset")
            for level in ("dl", "dh", "dl"):
                subprocess.run(["pinctrl", "set", str(self._gpio), "op", level],
                               check=True, capture_output=True)
                time.sleep(0.1)

    def configure(self, radio, transmit_enabled=False):
        if radio.region != "US" or radio.sync_word != 0x2B:
            raise ValueError("Experimental RAK2247 backend supports US / sync 0x2B only")
        hz, bw, sf = int(radio.frequency_mhz * 1e6), int(radio.bandwidth_khz), radio.spreading_factor
        if bw not in (125, 250, 500) or not 7 <= sf <= 12:
            raise ValueError("Unsupported SX1301 bandwidth/spreading factor")
        if not 902000000 + bw * 500 <= hz <= 928000000 - bw * 500:
            raise ValueError("SX1301 channel extends outside US band")
        if transmit_enabled and not self._gains:
            raise ValueError("SX1301 TX requires the verified carrier gain table")
        with self._lock:
            if self._lib.mp1301_configure(hz, sf, bw, self._clksrc, int(transmit_enabled)):
                raise RuntimeError("SX1301 configuration failed")
            for gain in self._gains:
                if self._lib.mp1301_add_gain(*(gain[k] for k in
                    ("dig_gain", "pa_gain", "dac_gain", "mix_gain", "rf_power"))):
                    raise ValueError("Invalid SX1301 TX gain table")
            self._radio, self._tx = radio, transmit_enabled

    def start(self):
        with self._lock:
            if self._lib.mp1301_start():
                raise RuntimeError("SX1301 startup or sync-word verification failed")
            self._started = True

    def stop(self):
        with self._lock:
            if self._started:
                self._started = False
                if self._lib.mp1301_stop():
                    raise RuntimeError("SX1301 cleanup failed")

    @property
    def crc_bad_count(self):
        with self._lock:
            return self._lib.mp1301_crc_bad() if self._lib else 0

    @property
    def no_crc_count(self):
        with self._lock:
            return self._lib.mp1301_no_crc() if self._lib else 0

    def receive(self):
        with self._lock:
            n = self._lib.mp1301_receive()
            if n < 0 or n > 16:
                raise RuntimeError("SX1301 receive failed")
            result = []
            for i in range(n):
                payload = (c.c_ubyte * 256)()
                hz, ticks = c.c_uint32(), c.c_uint32()
                sf, bw, cr = c.c_int(), c.c_int(), c.c_int()
                rssi, snr = c.c_float(), c.c_float()
                size = self._lib.mp1301_packet(i, payload, 256, c.byref(hz), c.byref(ticks),
                    c.byref(sf), c.byref(bw), c.byref(rssi), c.byref(snr), c.byref(cr))
                if size < 1 or size > 255:
                    raise RuntimeError("Invalid SX1301 native packet")
                result.append(dict(payload=bytes(payload[:size]), frequency_hz=hz.value,
                    timestamp_us=ticks.value, spreading_factor=sf.value,
                    bandwidth_khz=bw.value, coderate=cr.value, rssi=rssi.value, snr=snr.value))
            return result

    def read_channel_diagnostics(self):
        """Read raw registers under RX/TX ownership; never infer channel clear.

        RSSI units/path, status bits, update latency and weak-signal detection
        need independent validation for the configured standard LoRa modem.
        Missing/failed diagnostics are errors, never an idle-channel result.
        """
        names = ("modem_status", "valid_headers_std", "valid_packets_std",
                 "channel_rssi_raw", "baseband_rssi_raw", "decimator_rssi_raw",
                 "rssi_channel_selection", "std_radio_selection")
        with self._lock:
            if not self._started:
                raise RuntimeError("SX1301 is not running")
            read = getattr(self._lib, "mp1301_channel_diagnostics", None)
            if read is None:
                raise NotImplementedError("Native library lacks passive diagnostics")
            values = (c.c_int32 * len(names))()
            before = time.monotonic()
            if read(values, len(names)):
                raise RuntimeError("SX1301 diagnostic register read failed")
            return dict(registers=dict(zip(names, values)),
                        monotonic_before=before, monotonic_after=time.monotonic(),
                        channel_clear=None, calibrated=False)

    def build_tx_packet(self, payload, radio, config):
        packet = SX1301TxPacket(bytes(payload), int(radio.frequency_mhz * 1e6),
            radio.spreading_factor, int(radio.bandwidth_khz),
            int(radio.coding_rate.split("/")[1]) - 4, config.tx_power_dbm)
        self._validate_packet(packet)
        return packet

    def _validate_packet(self, p):
        if not isinstance(p, SX1301TxPacket) or not 16 <= p.size <= 255:
            raise ValueError("Invalid SX1301 TX packet")
        r = self._radio
        if r is None or (p.freq_hz, p.datarate, p.bandwidth) != (
            int(r.frequency_mhz * 1e6), r.spreading_factor, int(r.bandwidth_khz)):
            raise ValueError("SX1301 TX must match configured receiver")
        if p.rf_power not in {g["rf_power"] for g in self._gains} or not 1 <= p.coderate <= 4:
            raise ValueError("Unsupported SX1301 power or coding rate")

    def _tx_call(self, name, packet):
        self._validate_packet(packet)
        payload = (c.c_ubyte * packet.size).from_buffer_copy(packet.payload)
        return getattr(self._lib, name)(payload, packet.size, packet.rf_power, packet.coderate)

    def send(self, packet):
        with self._lock:
            if not self._started or not self._tx:
                return -1
            return self._tx_call("mp1301_send", packet)

    def get_tx_status(self, rf_chain=0):
        if rf_chain != 0:
            raise ValueError("SX1301 TX uses RF chain 0")
        with self._lock:
            return self._lib.mp1301_tx_status()

    def get_time_on_air(self, packet):
        with self._lock:
            value = self._tx_call("mp1301_airtime", packet)
            if value <= 0:
                raise RuntimeError("SX1301 airtime calculation failed")
            return value
