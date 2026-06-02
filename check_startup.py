#!/usr/bin/env python3
"""
Run this FIRST to diagnose import errors and kill any stuck server.
Usage:  python check_startup.py
"""
import sys
import os
import traceback
import subprocess
import platform

print("\n" + "="*55)
print("  AI Paper Trader — Startup Diagnostics")
print("="*55)

# ── 1. Kill any process on port 8000 ─────────────────────────────────────
print("\n[1] Checking port 8000 ...")
try:
    if platform.system() == "Windows":
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5
        )
        pids = set()
        for line in result.stdout.splitlines():
            if ":8000 " in line and "LISTENING" in line:
                parts = line.strip().split()
                if parts:
                    pids.add(parts[-1])
        if pids:
            for pid in pids:
                try:
                    subprocess.run(["taskkill", "/PID", pid, "/F"],
                                   capture_output=True, timeout=5)
                    print(f"  ✓  Killed PID {pid} (was holding port 8000)")
                except Exception as e:
                    print(f"  ~  Could not kill PID {pid}: {e}")
        else:
            print("  ✓  Port 8000 is free")
    else:
        subprocess.run(["fuser", "-k", "8000/tcp"],
                       capture_output=True, timeout=5)
        print("  ✓  Port 8000 released (Unix)")
except Exception as e:
    print(f"  ~  Port check skipped: {e}")

# ── 2. Test each import ───────────────────────────────────────────────────
print("\n[2] Testing imports ...")
errors: list[tuple[str, str]] = []

def test(label: str, code: str):
    try:
        exec(code, {})
        print(f"  ✓  {label}")
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"  ✗  {label}: {exc}")
        errors.append((label, tb))

test("config",                "import config")
test("database.models",       "from database.models import init_db")
test("database.db",           "from database import db")
test("trading.market",        "from trading.market import get_quote")
test("trading.portfolio",     "from trading.portfolio import get_portfolio_summary")
test("trading.engine",        "from trading.engine import buy, sell")
test("research.analyst",      "from research.analyst import analyse")
test("routers.dashboard",     "from routers import dashboard")
test("routers.research",      "from routers import research")
test("routers.trade",         "from routers import trade")
test("routers.positions",     "from routers import positions")
test("routers.transactions",  "from routers import transactions")
test("routers.reports",       "from routers import reports")
test("routers.predictions",   "from routers import predictions")
test("routers.diagnostics",   "from routers import diagnostics")
test("routers.auto_trading",  "from routers import auto_trading")
test("trading.auto_trader",   "from trading import auto_trader")

# ── 3. Summary ────────────────────────────────────────────────────────────
print("\n" + "="*55)
if errors:
    print(f"  FAILED: {len(errors)} import(s) broken\n")
    for name, tb in errors:
        print(f"  ── {name} ──────────────────────────────────")
        print(tb)
else:
    print("  ALL IMPORTS OK — safe to run:  python web_app.py")

print("="*55 + "\n")
sys.exit(1 if errors else 0)
