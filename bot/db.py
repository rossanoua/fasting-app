"""Tiny SQLite wrapper for per-user fasting state.

Schema:
    users(
        user_id          PK
        chat_id          (where to send notifications)
        fast_start_ts    int unix-seconds, null if not currently fasting
        target_hours     int
        eating_hours     int
        fast_notified    bool — sent "target reached" already?
        eating_start_ts  int unix-seconds, null if not currently in eating window
        eating_notified  bool — sent "time to start fasting" already?
    )

Single-table, idempotent upserts. No migrations needed for v1.
"""

from __future__ import annotations

import aiosqlite
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "fasting.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    fast_start_ts INTEGER,
    target_hours INTEGER,
    eating_hours INTEGER,
    fast_notified INTEGER DEFAULT 0,
    eating_start_ts INTEGER,
    eating_notified INTEGER DEFAULT 0
);
"""


async def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def start_fast(user_id: int, chat_id: int, target_hours: int,
                      eating_hours: int, now_ts: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, chat_id, fast_start_ts, target_hours,
                                eating_hours, fast_notified, eating_start_ts,
                                eating_notified)
            VALUES (?, ?, ?, ?, ?, 0, NULL, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id=excluded.chat_id,
                fast_start_ts=excluded.fast_start_ts,
                target_hours=excluded.target_hours,
                eating_hours=excluded.eating_hours,
                fast_notified=0,
                eating_start_ts=NULL,
                eating_notified=0
            """,
            (user_id, chat_id, now_ts, target_hours, eating_hours),
        )
        await db.commit()


async def stop_all(user_id: int) -> None:
    """User manually stopped — clear all active timers."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users SET
                fast_start_ts=NULL, target_hours=NULL,
                eating_start_ts=NULL, eating_hours=NULL,
                fast_notified=0, eating_notified=0
            WHERE user_id=?
            """,
            (user_id,),
        )
        await db.commit()


async def mark_fast_notified(user_id: int, now_ts: int) -> None:
    """Mark "target reached" notification sent + transition to eating window."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users SET
                fast_notified=1,
                eating_start_ts=?,
                eating_notified=0
            WHERE user_id=?
            """,
            (now_ts, user_id),
        )
        await db.commit()


async def mark_eating_notified(user_id: int) -> None:
    """Mark "time to fast" notification sent + clear all timers (waiting for user)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users SET
                eating_notified=1,
                fast_start_ts=NULL, target_hours=NULL,
                eating_start_ts=NULL, eating_hours=NULL
            WHERE user_id=?
            """,
            (user_id,),
        )
        await db.commit()


async def get_status(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        )
        row = await cur.fetchone()
        cols = [d[0] for d in cur.description] if cur.description else []
        await cur.close()
        if not row:
            return None
        return dict(zip(cols, row))


async def all_due_for_fast_notification(now_ts: int) -> list[dict]:
    """Users whose fasting target reached AND not yet notified."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT user_id, chat_id, fast_start_ts, target_hours, eating_hours
            FROM users
            WHERE fast_start_ts IS NOT NULL
              AND fast_notified = 0
              AND (? - fast_start_ts) >= (target_hours * 3600)
            """,
            (now_ts,),
        )
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        await cur.close()
        return [dict(zip(cols, r)) for r in rows]


async def all_due_for_eating_notification(now_ts: int) -> list[dict]:
    """Users whose eating window ended AND not yet notified."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT user_id, chat_id, eating_start_ts, eating_hours
            FROM users
            WHERE eating_start_ts IS NOT NULL
              AND eating_notified = 0
              AND (? - eating_start_ts) >= (eating_hours * 3600)
            """,
            (now_ts,),
        )
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        await cur.close()
        return [dict(zip(cols, r)) for r in rows]
