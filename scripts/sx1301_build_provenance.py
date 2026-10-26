#!/usr/bin/env python3
"""Reject uncommitted HAL inputs and record a checkout-local build manifest."""
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path


def git(hal, *args):
    return subprocess.check_output(["git", "-C", str(hal), *args])


def check(hal):
    if git(hal, "status", "--porcelain", "--untracked-files=no").strip():
        raise SystemExit("HAL tracked files must be clean; commit source changes before building")
    others = git(hal, "ls-files", "--others", "-z").decode().split("\0")
    source = [p for p in others if p and Path(p).suffix in (".c", ".h", ".var", ".cfg")
              and p != "libloragw/inc/config.h"]
    if source:
        raise SystemExit("Untracked HAL source inputs must be committed: " + ", ".join(source))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    mode, hal = sys.argv[1], Path(sys.argv[2]).resolve()
    check(hal)
    if mode == "check":
        return
    if mode != "record":
        raise SystemExit("Expected check or record")
    root, compiler = Path(sys.argv[3]).resolve(), sys.argv[4]
    paths = git(hal, "ls-files", "-z").decode().split("\0")
    files = {p: sha(hal / p) for p in paths if p}
    document = {
        "native_abi": 2,
        "build_platform": platform.platform(),
        "hal_revision": git(hal, "rev-parse", "HEAD").decode().strip(),
        "hal_tracked_clean": True,
        "hal_untracked_source_inputs": [],
        "hal_tracked_sha256": files,
        "hal_generated_config_sha256": sha(hal / "libloragw/inc/config.h"),
        "hal_archive_sha256": sha(hal / "libloragw/libloragw.a"),
        "meshpoint_revision": git(root, "rev-parse", "HEAD").decode().strip(),
        "meshpoint_tracked_status": git(root, "status", "--porcelain", "--untracked-files=no").decode(),
        "shim_sha256": sha(root / "scripts/sx1301_native.c"),
        "build_script_sha256": sha(root / "scripts/build_sx1301_native.sh"),
        "library_sha256": sha(root / "build/libsx1301_meshpoint.so"),
        "shim_compiler": compiler,
        "shim_compiler_version": subprocess.check_output([compiler, "--version"], text=True).splitlines()[0],
        "hal_compiler": "gcc (Semtech Makefile default; no CROSS_COMPILE override)",
        "hal_compiler_version": subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0],
        "hal_debug_options": {"DEBUG_HAL": 1, "DEBUG_REG": 1},
        "hal_cflags": "-O2 -Wall -Wextra -std=c99 -fPIC -Iinc -I.",
        "shim_cflags": "-O2 -Wall -Wextra -Werror -std=c99 -fPIC -shared",
        "shim_ldflags": "-lrt -lm",
        "host_spi_hz": 8000000,
    }
    (root / "build/sx1301-build-provenance.json").write_text(json.dumps(document, indent=2) + "\n")


if __name__ == "__main__":
    main()
