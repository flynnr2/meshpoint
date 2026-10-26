/* Fake HAL for the real SX1301 shim: no radio, GPIO, SPI or wall-clock waits. */
#include <assert.h>
#include <stdio.h>
#include "../../scripts/sx1301_native.c"

static int mode, polls, accepted, emitted_preamble, timed_preamble;
static int32_t regs[1024];
int lgw_board_setconf(struct lgw_conf_board_s conf) { (void)conf; return 0; }
int lgw_rxrf_setconf(uint8_t chain, struct lgw_conf_rxrf_s conf) {
    (void)chain; (void)conf; return 0;
}
int lgw_rxif_setconf(uint8_t chain, struct lgw_conf_rxif_s conf) {
    (void)chain; (void)conf; return 0;
}
int lgw_txgain_setconf(struct lgw_tx_gain_lut_s *conf) { (void)conf; return 0; }
int lgw_start(void) { return 0; }
int lgw_stop(void) { return 0; }
int lgw_receive(uint8_t max, struct lgw_pkt_rx_s *packets) {
    (void)max; (void)packets; return 0;
}
int lgw_reg_w(uint16_t id, int32_t value) { assert(id < 1024); regs[id] = value; return 0; }
int lgw_reg_r(uint16_t id, int32_t *value) { assert(id < 1024); *value = regs[id]; return 0; }
void wait_ms(unsigned long ms) { (void)ms; }
uint32_t lgw_time_on_air(struct lgw_pkt_tx_s *packet) {
    timed_preamble = packet->preamble;
    return packet->preamble * 10U + packet->size;
}
int lgw_send(struct lgw_pkt_tx_s packet) {
    emitted_preamble = packet.preamble;
    accepted = 1; polls = 0;
    return mode == 3 ? -1 : 0;
}
int lgw_status(uint8_t select, uint8_t *status) {
    (void)select;
    if (!accepted) { *status = mode == 4 ? TX_SCHEDULED : TX_FREE; return 0; }
    if (mode == 1) {
        if (polls++) return -1;
        *status = TX_EMITTING; return 0;
    }
    if (mode == 2) { *status = TX_SCHEDULED; return 0; }
    *status = polls++ == 0 ? TX_EMITTING : TX_FREE;
    return 0;
}
int main(void) {
    const uint8_t payload[24] = {0};
    const int lengths[] = {6, 8, 16, 32, 65533};
    assert(mp1301_abi_version() == 2);
    assert(mp1301_configure(906875000, 11, 250, 1, 1, 5) == -1);
    assert(mp1301_configure(906875000, 11, 250, 1, 1, 65534) == -1);
    for (unsigned i = 0; i < sizeof(lengths) / sizeof(lengths[0]); i++) {
        assert(mp1301_configure(906875000, 11, 250, 1, 1, lengths[i]) == 0);
        assert(mp1301_add_gain(0, 1, 3, 12, 10) == 0);
        assert(mp1301_start() == 0);
        assert(mp1301_airtime(payload, sizeof(payload), 10, 1) == lengths[i] * 10 + 24);
        assert(timed_preamble == lengths[i]);
        for (mode = 0; mode <= 4; mode++) {
            accepted = polls = 0;
            int result = mp1301_send(payload, sizeof(payload), 10, 1);
            assert(result == (mode == 0 ? 0 : -1));
            assert(mp1301_last_tx_observed() == (mode == 0 || mode == 1));
            if (mode != 4) assert(emitted_preamble == lengths[i]);
        }
        assert(mp1301_stop() == 0);
    }
    puts("Native ABI, preamble/airtime, emission failure, timeout and stale-state controls passed");
    return 0;
}
