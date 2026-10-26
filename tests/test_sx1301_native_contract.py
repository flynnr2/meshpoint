"""Compile the actual C shim against optional Semtech v5.0.1 headers and a fake HAL.

Set SX1301_HAL_DIR to a clean local lora_gateway checkout to enable this check.
It needs a C compiler but never links the hardware HAL or accesses a radio.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_real_native_shim_preamble_airtime_and_failure_contract(tmp_path):
    hal = os.environ.get("SX1301_HAL_DIR")
    if not hal:
        pytest.skip("Set SX1301_HAL_DIR to Semtech HAL 5.0.1 for native contract test")
    assert (Path(hal) / "VERSION").read_text().strip() == "5.0.1"
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("Native contract test requires a C compiler")
    root = Path(__file__).resolve().parents[1]
    # Header-only build configuration; no HAL code or generated files are changed.
    (tmp_path / "config.h").write_text('#define LIBLORAGW_VERSION "5.0.1"\n')
    binary = tmp_path / "native-contract"
    subprocess.run([compiler, "-Wall", "-Wextra", "-Werror", "-std=c99",
                    "-I" + str(Path(hal) / "libloragw/inc"), "-I" + str(tmp_path),
                    str(root / "tests/fixtures/sx1301_hal_stub.c"), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
    assert "controls passed" in result.stdout
