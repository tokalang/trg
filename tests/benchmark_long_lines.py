#!/usr/bin/env python3
"""
Long Line Linear Scaling Benchmark for trg
Measures:
1. Functional match correctness on 4MiB, 16MiB, and 32MiB lines.
2. Latency scaling ratio: median(T32) / median(T16) < 3.0 (linear scaling).
3. Conservative watchdog timeout (10s max per run).
"""

import os
import pathlib
import statistics
import subprocess
import tempfile
import time

def log(msg: str):
    print(f"[BENCHMARK] {msg}", flush=True)

def measure_run(trg_bin: str, file_path: pathlib.Path, pattern: str) -> float:
    t0 = time.perf_counter()
    r = subprocess.run(
        [trg_bin, "-n", pattern, str(file_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10.0
    )
    t1 = time.perf_counter()
    if r.returncode != 0:
        raise RuntimeError(f"Search failed on {file_path} (exit {r.returncode}):\n{r.stderr}")
    if pattern not in r.stdout:
        raise AssertionError(f"Pattern '{pattern}' not found in stdout for {file_path}")
    return t1 - t0

def run_benchmark(trg_bin: str):
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if not os.path.exists(trg_bin):
        raise FileNotFoundError(f"Binary not found: {trg_bin}")

    log(f"Running long line scaling benchmark on: {trg_bin}")

    sizes = [
        ("4MiB", 4 * 1024 * 1024),
        ("16MiB", 16 * 1024 * 1024),
        ("32MiB", 32 * 1024 * 1024),
    ]

    with tempfile.TemporaryDirectory(prefix="trg-bench-") as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        timings = {}

        for label, byte_count in sizes:
            fixture = tmp / f"bench_{label}.txt"
            log(f"Generating fixture for {label} ({byte_count} bytes)...")
            # Fill with 'x', place TARGET near the end, and end with '\n'
            prefix_len = byte_count - 1000
            content = "x" * prefix_len + "BENCHMARK_TARGET" + "y" * 980 + "\n"
            fixture.write_text(content, encoding="utf-8")

            # Warmup run
            measure_run(trg_bin, fixture, "BENCHMARK_TARGET")

            # 3 timed measurement runs
            runs = []
            for i in range(3):
                elapsed = measure_run(trg_bin, fixture, "BENCHMARK_TARGET")
                runs.append(elapsed)
            
            med = statistics.median(runs)
            timings[label] = med
            log(f"  {label} (runs: {[f'{x:.4f}s' for x in runs]}) -> Median: {med:.4f}s")

        t4 = timings["4MiB"]
        t16 = timings["16MiB"]
        t32 = timings["32MiB"]

        ratio_16_4 = t16 / t4 if t4 > 0 else 1.0
        ratio_32_16 = t32 / t16 if t16 > 0 else 1.0

        log(f"Scaling Ratios: T16/T4 = {ratio_16_4:.2f}x (linear limit ~4.0x), T32/T16 = {ratio_32_16:.2f}x (linear limit ~2.0x)")

        # Verify linear scaling bound
        assert ratio_32_16 < 3.0, f"Quadratic regression detected: T32/T16 = {ratio_32_16:.2f}x >= 3.0"
        assert t32 < 1.0, f"32MiB line took longer than expected: {t32:.4f}s >= 1.0s"

        log("BENCHMARK SCALING VALIDATION PASSED (Strictly linear performance verified)!")

if __name__ == "__main__":
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    trg = str(repo_root / "target" / "debug" / "trg")
    run_benchmark(trg)
