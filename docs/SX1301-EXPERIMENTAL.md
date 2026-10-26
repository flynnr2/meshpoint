# Experimental SX1301 backend

This contribution adds an opt-in SX1301 adapter to the existing Meshpoint capture and transmit interfaces. SX1302 remains the default. It addresses the hardware-support request in [issue #91](https://github.com/KMX415/meshpoint/issues/91). This branch is local and has not been submitted upstream. AI assistance was used.

## Hardware scope

Validated hardware is an original Helium OG carrier, RAK2247 **SPI US915 / SX1301 / SX1257**, Raspberry Pi **3 Model B+**, running Debian 13 aarch64. The recovered carrier setup uses SPI0.0, BCM25 reset, RF1 as clock source and RSSI offset -166. Other SX1301 carriers must establish their own wiring and gain table.

Only the single-SF IF8 modem is enabled: one frequency and one selected SF7–SF12 / 125, 250 or 500 kHz preset at a time. There is no eight-channel 250 kHz reception, spectral scan or automatic multi-preset operation. The current validation accepts US 902–928 MHz settings and Meshtastic sync word 0x2B only.

The private C shim builds against Semtech HAL 5.0.1 headers and exposes a primitive, versioned ABI. It does not reuse SX1302 C structures. A lock serializes RX, TX and stop. RX requires valid CRC and valid metadata. TX requires explicit enablement and a carrier gain table, verifies parameters and power membership, and reports emission only after TX_EMITTING followed by TX_FREE. These observations do not measure RF output or demonstrate delivery to another radio.

## Build and configuration

Use a dedicated checkout and normal SPI/GPIO permissions. The upstream installer is unchanged and does not install this backend.

The exact companion driver is published separately at
[`79871283e08c45e89d1196a5469e1aa68f544e71`](https://github.com/flynnr2/lora_gateway/tree/79871283e08c45e89d1196a5469e1aa68f544e71).
Review the [complete four-line timing diff against v5.0.1](https://github.com/flynnr2/lora_gateway/compare/a955619271b5d0a46d32e08150acfbc1eed183b7...79871283e08c45e89d1196a5469e1aa68f544e71).
It is a separate experimental driver dependency, not vendored Meshpoint source.

For a fresh Linux/Pi candidate, with a C toolchain, Git, Python 3 and Make:

```sh
git clone https://github.com/flynnr2/lora_gateway.git ~/lora_gateway_sx1301
cd ~/lora_gateway_sx1301
git checkout --detach 79871283e08c45e89d1196a5469e1aa68f544e71

git clone https://github.com/flynnr2/meshpoint.git ~/meshpoint_sx1301_candidate
cd ~/meshpoint_sx1301_candidate
git checkout submission/sx1301-backend-20261025
./scripts/build_sx1301_native.sh ~/lora_gateway_sx1301
```

The script rebuilds a position-independent HAL archive and writes checkout-local
`build/libsx1301_meshpoint.so`, `build/sx1301-hal-revision.txt` and
`build/sx1301-build-provenance.json`. It never replaces system libloragw. Native
ABI **2** is required: older ABI-1 libraries are rejected and must be rebuilt.
The manifest records all tracked HAL file hashes, revision, generated-config,
archive, shim and library hashes, compiler versions/flags and the Meshpoint
revision/status. Dirty tracked HAL files or additional untracked source/header
inputs are rejected before building. Generated `libloragw/inc/config.h` is the
only untracked header permitted; Make regenerates it during the forced build.
Build without external Make/compiler flag overrides to reproduce this recipe.
Retain the manifest with the hardware test results and verify the library path
and hash actually mapped by the running process separately from the checkout.

Stock HAL startup was unreliable on the tested board. Successful historical
runs used the companion patch: 1 ms internal radio-SPI completion delays and
10 ms MCU reset-release delays. Host SPI remains at the stock 8 MHz; the old
directory name `lora_gateway_spi1mhz` was not evidence of its active bus speed.
Neither the timing patch nor the rate difference from recovered Helium firmware
establishes a universal hardware fix.

Historical provenance limit: archived driver revision records identify 7987128,
and the archived main `loragw_hal.c` SHA-256
`14eaa8251e1bdddd51fea273a9b49278702522ae96a8f6480aa4e5ef0c92939a`
matches that commit. The earlier records do not establish the absence of every
uncommitted file throughout the historical HAL tree. A read-only re-audit on
26 October was blocked by SSH authentication; no historical-clean-tree claim
is made. The clean recipe above deliberately requires **no additional HAL
patches**. Its revised ABI-2 candidate still needs a fresh Linux link/build and
hardware validation; the old binary and RF results do not validate this revision.

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

## Review follow-up: TX and airtime

The configured `radio.preamble_length` now supplies both RX (plus two SX1301
sync symbols), native TX, and the HAL airtime calculation. The ABI-2 configure
call passes that value to the shim; TX packets with a different preamble are
rejected. Bounds are 6 through 65533 symbols. Changing the preamble requires
stop/configure/start, as with other radio settings.

The Meshpoint TX service reserves the full calculated airtime **before** each
SX1301 native dispatch. A rejection, completion/status failure after emission,
unknown result, exception or cancellation retains the reservation. Successful
sends are charged once, with no second success charge. Attempts rejected by the
service duty-budget check never dispatch or incur a charge. A HAL airtime
calculation error aborts before dispatch rather than falling back to a
fixed-preamble estimate. This is conservative
budget accounting, not evidence that every charged attempt emitted or delivered
a frame. SX1302 retains its existing success-based accounting.

For a repeatable native contract check without radio access, after installing
Meshpoint's Python test requirements and checking out the pinned HAL above:

```sh
SX1301_HAL_DIR=~/lora_gateway_sx1301 python -m pytest \
  tests/test_sx1301_backend.py tests/test_sx1301_native_contract.py \
  tests/test_sx1301_build_provenance.py -q
```

The native check compiles the real shim against actual Semtech headers with a
fake HAL. It exercises configured preamble/airtime, observed emission followed
by status failure, timeout and rejected sends, and clearing stale emission
state. It never links the hardware HAL or opens SPI/GPIO. Without
`SX1301_HAL_DIR`, that optional native test reports a skip, not a hardware pass.

## Contribution boundary

This branch contains the hardware adapter, build helper, safe example configuration, integration hooks, hardware tests and this guide. It contains no experiment scheduler, OG13 announcements, reception-results dashboard, MQTT evidence watcher, traceroute scheduler, HNTOG user services, GNSS/chrony work or map artifacts.

Generic Meshtastic radio-header and return-route decoding corrections have their own independently testable branch. HNTOG's integrated experiment branch combines that work with this adapter for validation. Those layers must not be submitted as one large hardware-support PR. Follow [CONTRIBUTING.md](../CONTRIBUTING.md), discuss the architecture with maintainers and identify hardware/region limits explicitly.
