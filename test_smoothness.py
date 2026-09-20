#!/usr/bin/env python3
"""Quick validation that all programs run smoothly without hangs."""

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


def test_program(name: str, program) -> tuple[bool, str]:
    """Test a program for hangs and errors. Return (pass, message)."""
    max_time = 0.0
    iterations = 100

    try:
        for _ in range(iterations):
            t0 = time.perf_counter_ns()
            if hasattr(program, "_step"):
                program._step()
            t1 = time.perf_counter_ns()

            elapsed_ms = (t1 - t0) / 1_000_000
            max_time = max(max_time, elapsed_ms)

            if elapsed_ms > 10:  # Any single frame over 10ms is a hang
                return False, f"HANG detected: {elapsed_ms:.1f}ms"

        return True, f"OK ({max_time:.2f}ms max)"
    except Exception as e:
        return False, f"ERROR: {e}"


def main():
    print("\n" + "=" * 70)
    print("SMOOTHNESS VALIDATION TEST".center(70))
    print("=" * 70 + "\n")

    passed = 0
    failed = 0

    for name, program in PROGRAMS:
        success, msg = test_program(name, program)
        status = "✅" if success else "❌"
        print(f"{status} {name:<18} {msg}")

        if success:
            passed += 1
        else:
            failed += 1

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70 + "\n")

    if failed == 0:
        print("🎉 ALL PROGRAMS ARE SMOOTH - No hangs detected!")
        return 0
    else:
        print(f"⚠️  {failed} program(s) have issues")
        return 1


if __name__ == "__main__":
    sys.exit(main())
