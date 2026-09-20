#!/usr/bin/env python3
"""Benchmark all programs to identify CPU bottlenecks on Raspberry Pi."""

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from controller.programs.ball import Ball
from controller.programs.clock import Clock
from controller.programs.lava_lamp import LavaLamp
from controller.programs.metaballs import Metaballs
from controller.programs.moon import Moon
from controller.programs.perlin_terrain import PerlinTerrain
from controller.programs.snake import Snake
from controller.programs.tetris import Tetris
from controller.programs.ticker import Ticker

BENCHMARK_DURATION = 120  # seconds
PROGRAMS = [
    ("Clock", Clock()),
    ("Ball", Ball()),
    ("Snake", Snake()),
    ("Moon", Moon()),
    ("Metaballs", Metaballs()),
    ("LavaLamp", LavaLamp()),
    ("Ticker", Ticker()),
    ("PerlinTerrain", PerlinTerrain()),
    ("Tetris", Tetris()),
]


def benchmark_program(name: str, program) -> dict:
    """Benchmark a single program's _step() function."""
    print(f"\n🔍 Benchmarking {name}...", end=" ", flush=True)

    times = []
    start_time = time.perf_counter()
    iterations = 0
    max_iterations = 1000

    while (
        time.perf_counter() - start_time < BENCHMARK_DURATION
        and iterations < max_iterations
    ):
        t0 = time.perf_counter_ns()
        try:
            if hasattr(program, "_step"):
                program._step()
            else:
                # For programs without _step, call through the update flow
                pass
        except Exception as e:
            print(f"ERROR: {e}")
            return None
        t1 = time.perf_counter_ns()

        elapsed_ms = (t1 - t0) / 1_000_000
        times.append(elapsed_ms)
        iterations += 1

    if not times:
        print("No measurements")
        return None

    return {
        "name": name,
        "iterations": len(times),
        "avg": statistics.mean(times),
        "median": statistics.median(times),
        "min": min(times),
        "max": max(times),
        "stdev": statistics.stdev(times) if len(times) > 1 else 0,
        "p95": sorted(times)[int(len(times) * 0.95)],
        "p99": sorted(times)[int(len(times) * 0.99)],
    }


def print_report(results: list):
    """Print benchmark report in a clean format."""
    print("\n" + "=" * 80)
    print("BENCHMARK REPORT - Raspberry Pi CPU Performance".center(80))
    print("=" * 80)
    print("\nTarget: <2ms per frame for smooth 60 FPS (16ms frame time)")
    print("Safe: <1.5ms per frame (1.5ms / 16ms = 9.4% CPU budget)\n")

    header = (
        f"{'Program':<18} {'Avg':<8} {'Med':<8} {'Min':<8} "
        f"{'Max':<8} {'P95':<8} {'StdDev':<8} {'Status':<8}"
    )
    print(header)
    print("-" * 80)

    for r in sorted(results, key=lambda x: x["avg"], reverse=True):
        avg = r["avg"]
        status = "🔴 SLOW" if avg > 2.0 else "🟡 WARN" if avg > 1.5 else "🟢 OK"

        print(
            f"{r['name']:<18} "
            f"{r['avg']:>6.2f}ms "
            f"{r['median']:>6.2f}ms "
            f"{r['min']:>6.2f}ms "
            f"{r['max']:>6.2f}ms "
            f"{r['p95']:>6.2f}ms "
            f"{r['stdev']:>6.2f}ms "
            f"{status:<8}"
        )

    print("\n" + "=" * 80)
    print("KEY METRICS:")
    print("-" * 80)

    for r in sorted(results, key=lambda x: x["avg"], reverse=True):
        if r["avg"] > 1.5:
            print(f"\n⚠️  {r['name']} - OPTIMIZATION NEEDED")
            print(f"   Average: {r['avg']:.2f}ms (target <1.5ms)")
            print(f"   Max spike: {r['max']:.2f}ms")
            print(f"   Variability: ±{r['stdev']:.2f}ms (stdev)")
            print(f"   P99: {r['p99']:.2f}ms")

    print("\n" + "=" * 80)
    print("RECOMMENDATIONS:")
    print("-" * 80)

    slow_programs = [r for r in results if r["avg"] > 1.5]
    if slow_programs:
        print("\n🔧 Slow Programs Detected:")
        for r in slow_programs:
            if "Snake" in r["name"]:
                print(
                    f"   • {r['name']}: Consider caching more aggressively "
                    "(cache for 5+ frames)"
                )
            elif "Ticker" in r["name"]:
                print(f"   • {r['name']}: Cache API responses longer (5+ minutes)")
            elif "Metaballs" in r["name"]:
                print(f"   • {r['name']}: Use NumPy vectorization for blob rendering")
            else:
                print(
                    f"   • {r['name']}: Profile with time.perf_counter() "
                    "to find bottleneck"
                )
    else:
        print(
            "✅ All programs are within acceptable performance! No optimization needed."
        )

    print("\n" + "=" * 80)


def main():
    print("\n🚀 Starting 2-minute benchmark of all programs...")
    print(f"Target duration: {BENCHMARK_DURATION} seconds per program")

    results = []
    for name, program in PROGRAMS:
        result = benchmark_program(name, program)
        if result:
            results.append(result)
            print(f"✓ ({result['iterations']} iterations, avg {result['avg']:.2f}ms)")

    print_report(results)


if __name__ == "__main__":
    main()
