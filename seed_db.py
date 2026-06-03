"""
One-time DB seed script for Railway.
Copies essential data (account balance, positions, transactions) from
the bundled snapshot into the persistent data directory on first deploy.

Run via: python seed_db.py
Railway startup: only runs if the target DB is empty/new.
"""

import sqlite3
import os
from pathlib import Path

# Source: local snapshot bundled with the repo
SNAPSHOT = Path(__file__).parent / "db_snapshot.sqlite3"

# Target: Railway persistent data dir
DATA_DIR = Path(os.getenv("RAILWAY_DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TARGET = DATA_DIR / "trading.db"


def seed():
    if not SNAPSHOT.exists():
        print("[seed] No snapshot found — starting fresh")
        return

    if TARGET.exists():
        # Check if already seeded (has transactions)
        conn = sqlite3.connect(TARGET)
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()
        if count > 0:
            print(f"[seed] DB already has {count} transactions — skipping seed")
            return

    print(f"[seed] Copying snapshot → {TARGET}")
    import shutil
    shutil.copy2(SNAPSHOT, TARGET)
    print("[seed] Done")


if __name__ == "__main__":
    seed()
