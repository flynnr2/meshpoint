# Experimental SX1301 backend

This contribution adds an opt-in SX1301 adapter to the existing Meshpoint capture and transmit interfaces. SX1302 remains the default. It addresses the hardware-support request in [issue #91](https://github.com/KMX415/meshpoint/issues/91). This branch is local and has not been submitted upstream. AI assistance was used.

## Hardware scope

Validated hardware is an original Helium OG carrier, RAK2247 **SPI US915 / SX1301 / SX1257**, Raspberry Pi **3 Model B+**, running Debian 13 aarch64. The recovered carrier setup uses SPI0.0, BCM25 reset, RF1 as clock source and RSSI offset -166. Other SX1301 carriers must establish their own wiring and gain table.

Only the single-SF IF8 modem is enabled: one frequency and one selected SF7–SF12 / 125, 250 or 500 kHz preset at a time. There is no eight-channel 250 kHz reception, spectral scan or automatic multi-preset operation. The current validation accepts US 902–928 MHz settings and Meshtastic sync word 0x2B only.

The private C shim builds against Semtech HAL 5.0.1 headers and exposes a primitive, versioned ABI. It does not reuse SX1302 C structures. A lock serializes RX, TX and stop. RX requires valid CRC and valid metadata. TX requires explicit enablement and a carrier gain table, verifies parameters and power membership, and reports emission only after TX_EMITTING followed by TX_FREE. These observations do not measure RF output or demonstrate delivery to another radio.

## Build and configuration

Use a dedicated checkout and normal SPI/GPIO permissions. The upstream installer is unchanged and does not install this backend.

```sh
git clone --branch v5.0.1 https://github.com/Lora-net/lora_gateway.git ~/lora_gateway_sx1301
./scripts/build_sx1301_native.sh ~/lora_gateway_sx1301
```

The script rebuilds a position-independent HAL archive and writes checkout-local `build/libsx1301_meshpoint.so` plus `build/sx1301-hal-revision.txt`. It never replaces system libloragw. Reference HAL revision is `a955619271b5d0a46d32e08150acfbc1eed183b7`.

Stock HAL startup was unreliable on the tested board. Successful runs used a **separate companion HAL patch**, revision `79871283e08c45e89d1196a5469e1aa68f544e71`: 1 ms internal radio-SPI completion delays and 10 ms MCU reset-release delays. This patch is deliberately absent from the Meshpoint contribution. It retains stock 8 MHz host SPI; recovered Helium firmware used 1 MHz. Neither that rate difference nor the timing patch establishes a universal hardware fix.

Install Meshpoint's Python requirements and obtain activation through the normal setup. Copy `config/sx1301-helium.example.yaml` to private `config/local.yaml`, preserving existing authentication fields on a configured system. Choose a unique device identity before enabling TX. The example disables TX, relay, periodic broadcasts, cloud publishing and MQTT; no credential or experiment-specific OG13 identity is included.

The recovered gain table is carrier calibration input, not measured output or operating authorization. DAC gain 3 supplies the Semtech default omitted by the original firmware. Actual RF power, emissions and suitability of antenna/mode combinations require separate verification.

## Integration and validation

The adapter feeds the existing packet pipeline and native TX service; activation and authentication behavior are unchanged. Automatic SX1301 relay is rejected because hardware CAD/backoff is not implemented. A lock and duty budget do not replace carrier sensing. The optional explicit relay bench checks are maintained separately.

Synthetic tests cover backend selection, ABI compatibility, RX conversion, bounded payloads, gain/power/frequency validation, disabled TX, HAL failures and serialized RX/TX. Existing SX1302 behavior stays on its original path. Passing these tests does not establish an SX1302 hardware regression result.

Bench evidence includes decoded ambient Meshtastic packets and observed native transmissions. Independent RF returns and correlated traceroute replies were obtained by the separate HNTOG validation stack. Startup/calibration variability, RF output measurement, repeated cold boots and other carrier/region support remain open work.

## Receive preamble correction

Semtech HAL 5.0.1 leaves the single-SF service receiver preamble limit at 10
(eight preamble symbols plus two sync symbols). Meshtastic's configured default
is 16 preamble symbols. After each native start this backend writes the
configured length plus two to SX1301 register 204 and verifies it by readback.
A write error, read error or mismatch stops the concentrator and rejects startup.
Configuration must fit the 16-bit register, including the two sync symbols.

Controlled RAK2247/SX1301 results: TX8/RX10 received 6/6; reverting to
TX16/RX10 received 0/2; TX16/RX18 received 4/4 across two initializations.
The normal receiver subsequently received 2/2 from an independent native sender
using TX16. These small trials validate the observed mismatch and correction on
this board; they are not sensitivity or reliability estimates. The correction
was exercised in the experiment integration; this isolated contribution has
software checks and has not been newly installed as part of submission preparation.
Do not transfer the SX1301 plus-two rule to SX1302 register semantics.

Passive register diagnostics are raw, uncalibrated observations. In a separate
five-minute observation, MODEM_STATUS remained zero during known receptions;
zero status must not be interpreted as verified channel idle. This backend
provides no hardware CAD or clear-channel guarantee.

## Contribution boundary

This branch contains the hardware adapter, build helper, safe example configuration, integration hooks, hardware tests and this guide. It contains no experiment scheduler, OG13 announcements, reception-results dashboard, MQTT evidence watcher, traceroute scheduler, HNTOG user services, GNSS/chrony work or map artifacts.

Generic Meshtastic radio-header and return-route decoding corrections have their own independently testable branch. HNTOG's integrated experiment branch combines that work with this adapter for validation. Those layers must not be submitted as one large hardware-support PR. Follow [CONTRIBUTING.md](../CONTRIBUTING.md), discuss the architecture with maintainers and identify hardware/region limits explicitly.
