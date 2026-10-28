"""Database access, seeded demo data and deterministic reconciliation rules.

Financial quantities are integer minor units throughout; never binary floats.
The data is synthetic and does not represent a real payment processor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY,
  merchant TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  currency TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payment_status TEXT NOT NULL CHECK(payment_status IN ('captured', 'pending', 'failed')),
  channel TEXT NOT NULL,
  reviewed INTEGER NOT NULL DEFAULT 0 CHECK(reviewed IN (0, 1))
);
CREATE TABLE IF NOT EXISTS settlements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gateway_reference TEXT NOT NULL UNIQUE,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  currency TEXT NOT NULL,
  settled_at TEXT NOT NULL,
  bank_reference TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  details TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_created ON payments(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payments_merchant ON payments(merchant);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC);
"""
MERCHANTS = ["Northstar Commerce", "Atlas Market", "Pine & Co.", "Orbit Digital", "Summit Supply", "Bloom Retail"]
CHANNELS = ["Card", "ACH", "Wallet"]
EXCEPTIONS = {"amount_mismatch", "currency_mismatch", "missing_settlement"}
STATUSES = {"matched", "amount_mismatch", "currency_mismatch", "missing_settlement", "pending", "failed", "exceptions", "all"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def connect(db_path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")
    return db


def initialize(db_path: str | Path, seed: bool = True) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as db:
        db.executescript(SCHEMA)
        existing = db.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        if seed and not existing:
            seed_demo(db)


def seed_demo(db: sqlite3.Connection) -> None:
    rng = random.Random(4815)
    now = now_utc()
    payments = []
    settlements = []
    for i in range(420):
        date = now - timedelta(hours=(i * 2.05 + 1.7), minutes=i % 41)
        status = "failed" if i % 37 == 0 else ("pending" if i % 31 == 0 else "captured")
        amount = rng.randint(2500, 190000) + (i % 5) * 99
        reference = f"PAY-{date:%y%m%d}-{i+10001}"
        payments.append((reference, MERCHANTS[i % len(MERCHANTS)], amount, "USD", date.isoformat(), status, CHANNELS[i % len(CHANNELS)], 0))
        if status == "captured" and i % 13 != 0:
            settled = amount
            currency = "USD"
            if i % 23 == 0:
                settled += rng.choice([-1, 1]) * rng.randint(200, 2000)
            elif i % 47 == 0:
                currency = "EUR"
            settlements.append((reference, settled, currency, (date + timedelta(days=1)).isoformat(), f"BNK-{i+900100}"))
    db.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?, ?, ?)", payments)
    db.executemany("INSERT INTO settlements (gateway_reference, amount_cents, currency, settled_at, bank_reference) VALUES (?, ?, ?, ?, ?)", settlements)
    db.execute("INSERT INTO audit_events(created_at, actor, action, details) VALUES (?, ?, ?, ?)", (now.isoformat(), "system", "DEMO_DATA_SEEDED", f"Generated {len(payments)} synthetic payments and {len(settlements)} settlements"))


def classify(item: dict, now: datetime | None = None) -> dict:
    """Return reconciliation status and severity for a single joined payment."""
    now = now or now_utc()
    if item["payment_status"] == "failed":
        status = "failed"
    elif item["payment_status"] == "pending":
        status = "pending"
    elif item["settlement_amount_cents"] is None:
        age = now - datetime.fromisoformat(item["created_at"])
        status = "missing_settlement" if age >= timedelta(hours=48) else "pending"
    elif item["settlement_currency"] != item["currency"]:
        status = "currency_mismatch"
    elif item["settlement_amount_cents"] != item["amount_cents"]:
        status = "amount_mismatch"
    else:
        status = "matched"
    is_exception = status in EXCEPTIONS
    amount_gap = abs(item["amount_cents"] - (item["settlement_amount_cents"] or 0))
    risk = "high" if is_exception and (item["amount_cents"] >= 100000 or (status == "amount_mismatch" and amount_gap >= 1000) or status == "currency_mismatch") else ("medium" if is_exception else "low")
    return {**item, "status": status, "risk": risk, "is_exception": is_exception, "reviewed": bool(item["reviewed"]), "amount_gap_cents": amount_gap if status == "amount_mismatch" else 0}


def list_joined(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute("""
        SELECT p.*, s.amount_cents AS settlement_amount_cents,
               s.currency AS settlement_currency, s.settled_at,
               s.bank_reference
        FROM payments p
        LEFT JOIN settlements s ON s.gateway_reference = p.id
        ORDER BY p.created_at DESC, p.id ASC
    """).fetchall()
    now = now_utc()
    return [classify(dict(r), now) for r in rows]


def get_summary(items: list[dict]) -> dict:
    captured = [x for x in items if x["payment_status"] == "captured"]
    reconcilable = [x for x in captured if x["status"] != "pending"]
    matched = [x for x in reconcilable if x["status"] == "matched"]
    exceptions = [x for x in items if x["is_exception"]]
    open_exceptions = [x for x in exceptions if not x["reviewed"]]
    totals = {
        "processed_volume_cents": sum(x["amount_cents"] for x in captured),
        "matched_volume_cents": sum(x["amount_cents"] for x in matched),
        "match_rate_pct": round(len(matched) / len(reconcilable) * 100, 1) if reconcilable else 0,
        "open_exceptions": len(open_exceptions),
        "high_risk_open": sum(x["risk"] == "high" for x in open_exceptions),
        "unmatched_volume_cents": sum(x["amount_cents"] for x in open_exceptions),
        "transactions": len(items),
        "reconcilable": len(reconcilable),
    }
    counts = {s: sum(x["status"] == s for x in items) for s in ("matched", "amount_mismatch", "currency_mismatch", "missing_settlement", "pending", "failed")}
    daily = {}
    for x in captured:
        day = x["created_at"][:10]
        bucket = daily.setdefault(day, {"date": day, "volume_cents": 0, "matched_cents": 0, "exceptions": 0})
        bucket["volume_cents"] += x["amount_cents"]
        if x["status"] == "matched":
            bucket["matched_cents"] += x["amount_cents"]
        if x["is_exception"]:
            bucket["exceptions"] += 1
    merchant_rows = {}
    for x in captured:
        entry = merchant_rows.setdefault(x["merchant"], {"name": x["merchant"], "volume_cents": 0, "transactions": 0, "exceptions": 0})
        entry["volume_cents"] += x["amount_cents"]
        entry["transactions"] += 1
        entry["exceptions"] += int(x["is_exception"])
    return {
        "totals": totals,
        "status_counts": counts,
        "daily_volume": sorted(daily.values(), key=lambda x: x["date"])[-14:],
        "merchants": sorted(merchant_rows.values(), key=lambda x: x["volume_cents"], reverse=True),
        "recent_exceptions": open_exceptions[:6],
    }
