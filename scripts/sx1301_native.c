/* SPDX-License-Identifier: AGPL-3.0-only
 * Small stable ABI over Semtech SX1301 HAL 5.0.1. HAL structs stay in C.
 * One process owns one concentrator; the Python caller serializes every call.
 */
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include <string.h>
#include <stdio.h>
#include "loragw_hal.h"
#include "loragw_reg.h"
#include "loragw_aux.h"

static struct lgw_pkt_rx_s rx[LGW_PKT_FIFO_SIZE];
static struct lgw_tx_gain_lut_s gains;
static unsigned count, bad_crc, no_crc;
static uint32_t frequency, rate;
static uint8_t bandwidth;
static bool started, transmit;
static int last_tx_observed;

int mp1301_abi_version(void) { return 1; }
int mp1301_last_tx_observed(void) { return last_tx_observed; }
unsigned mp1301_crc_bad(void) { return bad_crc; }
unsigned mp1301_no_crc(void) { return no_crc; }

int mp1301_configure(uint32_t hz, int sf, int bw, int clock_source, int tx) {
    if (started || hz < 902000000 || hz > 928000000 || sf < 7 || sf > 12 ||
        (bw != 125 && bw != 250 && bw != 500) ||
        (clock_source != 0 && clock_source != 1) || (tx != 0 && tx != 1)) return -1;
    frequency = hz;
    rate = 1U << (sf - 6);
    bandwidth = bw == 125 ? BW_125KHZ : bw == 250 ? BW_250KHZ : BW_500KHZ;
    transmit = tx;
    count = bad_crc = no_crc = 0;
    memset(&gains, 0, sizeof(gains));
    struct lgw_conf_board_s board = {.lorawan_public = false, .clksrc = clock_source};
    if (lgw_board_setconf(board)) return -1;
    for (unsigned i = 0; i < LGW_RF_CHAIN_NB; i++) {
        struct lgw_conf_rxrf_s rf = {
            /* Radio 1 supplies the clock but has no enabled IF channels.
             * Keep its recovered Helium US center away from the wanted RF0
             * carrier, matching the recovered clock-chain configuration.
             */
            .enable = true, .freq_hz = i == 0 ? hz : 905000000, .rssi_offset = -166.0f,
            .type = LGW_RADIO_TYPE_SX1257, .tx_enable = tx && i == 0,
            .tx_notch_freq = 129000,
        };
        if (lgw_rxrf_setconf(i, rf)) return -1;
    }
    for (unsigned i = 0; i < LGW_IF_CHAIN_NB; i++) {
        struct lgw_conf_rxif_s channel = {0};
        if (i == 8) {
            channel.enable = true;
            channel.rf_chain = 0;
            channel.bandwidth = bandwidth;
            channel.datarate = rate;
        }
        if (lgw_rxif_setconf(i, channel)) return -1;
    }
    return 0;
}

int mp1301_add_gain(int dig, int pa, int dac, int mix, int power) {
    if (started || gains.size >= TX_GAIN_LUT_SIZE_MAX || dig < 0 || dig > 3 ||
        pa < 0 || pa > 3 || dac != 3 || mix < 8 || mix > 15 ||
        power < -20 || power > 27 ||
        (gains.size && power <= gains.lut[gains.size - 1].rf_power)) return -1;
    gains.lut[gains.size++] = (struct lgw_tx_gain_s){
        .dig_gain = dig, .pa_gain = pa, .dac_gain = dac,
        .mix_gain = mix, .rf_power = power,
    };
    return 0;
}

static int verified_write(uint16_t id, int32_t value) {
    int32_t actual = -1;
    return lgw_reg_w(id, value) || lgw_reg_r(id, &actual) || actual != value ? -1 : 0;
}

int mp1301_stop(void) {
    int result = lgw_stop();
    started = false;
    return result;
}

int mp1301_start(void) {
    if (started || (transmit && (!gains.size || lgw_txgain_setconf(&gains)))) return -1;
    if (lgw_start()) { mp1301_stop(); return -1; }
    if (verified_write(LGW_FRAME_SYNCH_PEAK1_POS, 2) ||
        verified_write(LGW_FRAME_SYNCH_PEAK2_POS, 11) ||
        verified_write(LGW_MBWSSF_FRAME_SYNCH_PEAK1_POS, 2) ||
        verified_write(LGW_MBWSSF_FRAME_SYNCH_PEAK2_POS, 11) ||
        (transmit && (verified_write(LGW_TX_FRAME_SYNCH_PEAK1_POS, 2) ||
                      verified_write(LGW_TX_FRAME_SYNCH_PEAK2_POS, 11)))) {
        mp1301_stop(); return -1;
    }
    started = true;
    return 0;
}

int mp1301_receive(void) {
    if (!started) return -1;
    int n = lgw_receive(LGW_PKT_FIFO_SIZE, rx);
    if (n < 0 || n > LGW_PKT_FIFO_SIZE) return -1;
    count = 0;
    for (int i = 0; i < n; i++) {
        struct lgw_pkt_rx_s p = rx[i];
        if (p.status == STAT_CRC_BAD) { bad_crc++; continue; }
        if (p.status == STAT_NO_CRC) { no_crc++; continue; }
        if (p.status != STAT_CRC_OK || p.modulation != MOD_LORA || p.if_chain != 8 ||
            !p.size || p.size > 255 || !isfinite(p.rssi) || !isfinite(p.snr)) continue;
        rx[count++] = p;
    }
    return count;
}

int mp1301_packet(unsigned index, uint8_t *payload, unsigned capacity,
                  uint32_t *hz, uint32_t *ticks, int *sf, int *bw,
                  float *rssi, float *snr, int *cr) {
    if (index >= count || !payload || capacity < rx[index].size ||
        !hz || !ticks || !sf || !bw || !rssi || !snr || !cr) return -1;
    const struct lgw_pkt_rx_s *p = &rx[index];
    int actual_sf = 7;
    while (actual_sf <= 12 && p->datarate != (1U << (actual_sf - 6))) actual_sf++;
    if (actual_sf > 12 || (p->bandwidth != BW_125KHZ && p->bandwidth != BW_250KHZ &&
        p->bandwidth != BW_500KHZ) || p->coderate < 1 || p->coderate > 4) return -1;
    memcpy(payload, p->payload, p->size);
    *hz = p->freq_hz; *ticks = p->count_us; *sf = actual_sf;
    *bw = p->bandwidth == BW_125KHZ ? 125 : p->bandwidth == BW_250KHZ ? 250 : 500;
    *rssi = p->rssi; *snr = p->snr; *cr = p->coderate;
    return p->size;
}

static int tx_packet(struct lgw_pkt_tx_s *p, const uint8_t *payload,
                     unsigned size, int power, int cr) {
    if (!payload || size < 16 || size > 255 || cr < 1 || cr > 4) return -1;
    memset(p, 0, sizeof(*p));
    p->freq_hz = frequency; p->tx_mode = IMMEDIATE; p->rf_chain = 0;
    p->rf_power = power; p->modulation = MOD_LORA;
    p->bandwidth = bandwidth; p->datarate = rate; p->coderate = cr;
    p->preamble = 16; p->size = size;
    memcpy(p->payload, payload, size);
    return 0;
}

int mp1301_airtime(const uint8_t *payload, unsigned size, int power, int cr) {
    struct lgw_pkt_tx_s p;
    if (tx_packet(&p, payload, size, power, cr)) return -1;
    return lgw_time_on_air(&p);
}

int mp1301_tx_status(void) {
    uint8_t status;
    return !started || lgw_status(TX_STATUS, &status) ? -1 : status;
}

int mp1301_send(const uint8_t *payload, unsigned size, int power, int cr) {
    if (!started || !transmit || mp1301_tx_status() != TX_FREE) return -1;
    bool found = false;
    for (unsigned i = 0; i < gains.size; i++) if (gains.lut[i].rf_power == power) found = true;
    struct lgw_pkt_tx_s p;
    if (!found || tx_packet(&p, payload, size, power, cr) || lgw_send(p)) return -1;
    last_tx_observed = 0;
    /* HAL acceptance alone is not completion. Observe emission and return to idle. */
    unsigned limit = lgw_time_on_air(&p) + 2000;
    for (unsigned elapsed = 0; elapsed < limit; elapsed += 5) {
        wait_ms(5);
        int status = mp1301_tx_status();
        if (status < 0) return -1;
        if (status == TX_EMITTING) last_tx_observed = 1;
        if (status == TX_FREE) return last_tx_observed ? 0 : -1;
    }
    return -1;
}
