"""SX1301 adapter and existing TX/relay integration; synthetic frames, not RF proof."""
import struct
import threading
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from src.capture.sx1301_source import SX1301CaptureSource
from src.config import AppConfig, RadioConfig, TransmitConfig
from src.decode.crypto_service import CryptoService
from src.hal.sx1301_wrapper import SX1301Wrapper
from src.transmit.tx_service import TxService

GAIN = dict(dig_gain=0, pa_gain=1, dac_gain=3, mix_gain=12, rf_power=10)


def wrapper(tx=True):
    w = SX1301Wrapper("unused.so", tx_gain_lut=[GAIN])
    w._lib = MagicMock()
    for name in ("configure", "add_gain", "start", "stop", "send", "receive"):
        getattr(w._lib, "mp1301_" + name).return_value = 0
    registers = {}
    w._lib.lgw_reg_w.side_effect = lambda reg, value: registers.__setitem__(reg, value) or 0
    def read_register(reg, out):
        out._obj.value = registers.get(reg, 0)
        return 0
    w._lib.lgw_reg_r.side_effect = read_register
    w._lib.mp1301_airtime.return_value = 500
    w._lib.mp1301_tx_status.return_value = 2
    w.configure(RadioConfig(frequency_mhz=906.875), tx)
    w.start()
    return w


@pytest.mark.parametrize("changes", [dict(region="EU_868"), dict(sync_word=0x34),
    dict(bandwidth_khz=62), dict(spreading_factor=6), dict(frequency_mhz=902.0)])
def test_unsupported_radio_configuration_fails_before_native_calls(changes):
    w = SX1301Wrapper("unused.so")
    w._lib = MagicMock()
    with pytest.raises(ValueError):
        w.configure(replace(RadioConfig(frequency_mhz=906.875), **changes))
    w._lib.mp1301_configure.assert_not_called()


def test_tx_requires_verified_gain_table():
    w = SX1301Wrapper("unused.so")
    with pytest.raises(ValueError, match="gain table"):
        w.configure(RadioConfig(frequency_mhz=906.875), True)


def test_real_sx1301_parameters_not_sx1302_abi_values():
    w = wrapper()
    w._lib.mp1301_configure.assert_called_once_with(906875000, 11, 250, 1, 1)
    p = w.build_tx_packet(bytes(24), w._radio, TransmitConfig(tx_power_dbm=10))
    assert (p.datarate, p.bandwidth, p.coderate) == (11, 250, 1)
    assert p.preamble == 16 and not p.invert_pol and not p.no_crc


@pytest.mark.parametrize("size", [0, 15, 256])
def test_oversized_or_truncated_tx_never_reaches_hal(size):
    w = wrapper()
    with pytest.raises(ValueError):
        w.build_tx_packet(bytes(size), w._radio, TransmitConfig(tx_power_dbm=10))
    w._lib.mp1301_send.assert_not_called()


def test_receive_only_gate_and_stop_prevent_transmission():
    w = wrapper(tx=False)
    p = w.build_tx_packet(bytes(24), w._radio, TransmitConfig(tx_power_dbm=10))
    assert w.send(p) == -1
    w._lib.mp1301_send.assert_not_called()
    w.stop()
    assert w.send(p) == -1


def test_tx_rejects_changed_frequency_or_unlisted_power():
    w = wrapper()
    p = w.build_tx_packet(bytes(24), w._radio, TransmitConfig(tx_power_dbm=10))
    for invalid in [replace(p, freq_hz=915000000), replace(p, rf_power=27)]:
        with pytest.raises(ValueError):
            w.send(invalid)
    w._lib.mp1301_send.assert_not_called()


def test_incompatible_shim_abi_rejected(monkeypatch):
    lib = MagicMock()
    lib.mp1301_abi_version.return_value = 999
    monkeypatch.setattr("ctypes.CDLL", lambda _path: lib)
    w = SX1301Wrapper("unused.so")
    with pytest.raises(RuntimeError, match="ABI"):
        w.load()
    assert w._lib is None


def test_rx_and_tx_calls_cannot_overlap():
    w = wrapper()
    entered, release, sent = threading.Event(), threading.Event(), threading.Event()
    def blocked_receive():
        entered.set()
        assert release.wait(2)
        return 0
    w._lib.mp1301_receive.side_effect = blocked_receive
    w._lib.mp1301_send.side_effect = lambda *_a: sent.set() or 0
    p = w.build_tx_packet(bytes(24), w._radio, TransmitConfig(tx_power_dbm=10))
    rx = threading.Thread(target=w.receive)
    tx = threading.Thread(target=lambda: w.send(p))
    rx.start()
    assert entered.wait(2)
    tx.start()
    assert not sent.wait(.05)
    release.set()
    rx.join(2)
    tx.join(2)
    assert sent.is_set() and not rx.is_alive() and not tx.is_alive()


def test_passive_diagnostics_return_raw_values_without_clear_channel_claim():
    w = wrapper(tx=False)
    def read(values, capacity):
        assert capacity == 8
        for i in range(capacity):
            values[i] = i + 10
        return 0
    w._lib.mp1301_channel_diagnostics.side_effect = read
    result = w.read_channel_diagnostics()
    assert result["registers"]["valid_headers_std"] == 11
    assert result["channel_clear"] is None and not result["calibrated"]
    assert result["monotonic_after"] >= result["monotonic_before"]
    w._lib.mp1301_send.assert_not_called()


def test_unavailable_or_failed_diagnostics_never_mean_idle():
    w = wrapper(tx=False)
    w._lib.mp1301_channel_diagnostics.return_value = -1
    with pytest.raises(RuntimeError, match="register read failed"):
        w.read_channel_diagnostics()
    w._lib.mp1301_channel_diagnostics = None
    with pytest.raises(NotImplementedError):
        w.read_channel_diagnostics()
    w.stop()
    with pytest.raises(RuntimeError, match="not running"):
        w.read_channel_diagnostics()


def test_passive_diagnostics_share_the_receive_lock():
    w = wrapper(tx=False)
    entered, release, read = threading.Event(), threading.Event(), threading.Event()
    def blocked_receive():
        entered.set()
        assert release.wait(2)
        return 0
    w._lib.mp1301_receive.side_effect = blocked_receive
    w._lib.mp1301_channel_diagnostics.side_effect = lambda *_a: read.set() or 0
    rx = threading.Thread(target=w.receive)
    diagnostic = threading.Thread(target=w.read_channel_diagnostics)
    rx.start()
    assert entered.wait(2)
    diagnostic.start()
    assert not read.wait(.05)
    release.set()
    rx.join(2)
    diagnostic.join(2)
    assert read.is_set() and not diagnostic.is_alive() and not rx.is_alive()


def test_capture_factory_selects_sx1301_and_does_not_inject_sx1302_gains():
    from src.api.server import _build_pipeline, _inject_tx_gain_into_source
    from src.log_format import _describe_sources
    cfg = AppConfig()
    cfg.capture.sources = ["concentrator"]
    cfg.capture.concentrator_backend = "sx1301"
    cfg.capture.meshcore_usb.auto_detect = False
    coord = _build_pipeline(cfg)
    source = coord.capture_coordinator._sources[0]
    assert isinstance(source, SX1301CaptureSource)
    _inject_tx_gain_into_source(coord)
    assert "start" not in source.__dict__
    assert _describe_sources(cfg) == "concentrator (SX1301 single modem)"


@pytest.mark.asyncio
async def test_source_converts_live_capture_to_existing_pipeline():
    source = SX1301CaptureSource(AppConfig())
    source._wrapper = MagicMock()
    source._wrapper.receive.return_value = [dict(payload=b"test", frequency_hz=906875000,
        spreading_factor=11, bandwidth_khz=250, coderate=1, rssi=-100, snr=2)]
    await source.start()
    stream = source.packets()
    raw = await anext(stream)
    assert raw.payload == b"test" and raw.capture_source == "concentrator"
    assert raw.signal.coding_rate == "4/5"
    await stream.aclose()
    await source.stop()
    source._wrapper.stop.assert_called_once()


@pytest.mark.asyncio
async def test_existing_relay_path_preserves_identity_and_honors_rx_gate():
    w = wrapper()
    service = TxService(wrapper=w, crypto=CryptoService(), radio_config=w._radio,
        transmit_config=TransmitConfig(enabled=True, tx_power_dbm=10, node_id=0x12341234))
    raw = struct.pack("<IIIBBBB", 0xffffffff, 0x12345678, 999, 0x63, 8, 0, 0) + bytes(20)
    packets = []
    def record_send(payload, size, power, cr):
        packets.append(bytes(payload[:size]))
        return 0
    w._lib.mp1301_send.side_effect = record_send
    result = await service.send_raw_relay(raw)
    assert result.success
    assert packets[0][:12] == raw[:12] and packets[0][13:15] == raw[13:15]
    assert packets[0][16:] == raw[16:]
    assert packets[0][12] == 0x62
    w._tx = False
    assert not (await service.send_raw_relay(raw)).success
    assert len(packets) == 1


@pytest.mark.asyncio
async def test_native_failure_cannot_report_success():
    w = wrapper()
    w._lib.mp1301_send.return_value = -1
    service = TxService(wrapper=w, crypto=CryptoService(), radio_config=w._radio,
        transmit_config=TransmitConfig(enabled=True, tx_power_dbm=10, node_id=0x12341234))
    raw = struct.pack("<IIIBBBB", 0xffffffff, 0x12345678, 999, 0x63, 8, 0, 0) + bytes(20)
    assert not (await service.send_raw_relay(raw)).success


def test_receive_preamble_follows_configured_length():
    w = wrapper()
    w._lib.lgw_reg_w.assert_called_with(204, 18)
    w.stop()
    w.configure(RadioConfig(frequency_mhz=906.875, preamble_length=8), True)
    w.start()
    w._lib.lgw_reg_w.assert_called_with(204, 10)


def test_receive_preamble_verification_failure_stops_native_owner():
    w = wrapper()
    w.stop()
    w._lib.lgw_reg_r.side_effect = lambda reg, out: 0
    with pytest.raises(RuntimeError, match="preamble verification"):
        w.start()
    assert not w._started
    assert w._lib.mp1301_stop.call_count == 2


@pytest.mark.parametrize("length", [0, 65534, 16.5])
def test_receive_preamble_rejects_out_of_range_length(length):
    w = wrapper()
    w.stop()
    with pytest.raises(ValueError, match="preamble"):
        w.configure(RadioConfig(frequency_mhz=906.875, preamble_length=length), True)
