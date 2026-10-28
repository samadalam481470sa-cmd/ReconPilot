"""FastAPI routes for a local synthetic-data payment operations dashboard."""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from io import StringIO
import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.core import EXCEPTIONS, STATUSES, connect, get_summary, initialize, list_joined, now_utc

STATIC = Path(__file__).parent / "static"
DEFAULT_DB = Path(os.getenv("RECONPILOT_DB", "/tmp/reconpilot/reconpilot.db"))


class ReviewChange(BaseModel):
    reviewed: bool


def parse_amount(value: str) -> int:
    try:
        amount = Decimal(value)
        cents = amount * 100
        if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000000") or cents != cents.to_integral_value():
            raise ValueError("Amount must be positive and have at most two decimal places")
        return int(cents)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Amount must be a decimal number") from exc


def parse_settlement_csv(content: str) -> list[tuple]:
    reader = csv.DictReader(StringIO(content))
    columns = {"gateway_reference", "amount", "currency", "settlement_date", "bank_reference"}
    if not reader.fieldnames or not columns.issubset(reader.fieldnames):
        raise ValueError("CSV must include: gateway_reference,amount,currency,settlement_date,bank_reference")
    parsed = []
    for number, row in enumerate(reader, start=2):
        try:
            reference = (row["gateway_reference"] or "").strip()
            bank_ref = (row["bank_reference"] or "").strip()
            currency = (row["currency"] or "").strip().upper()
            date = (row["settlement_date"] or "").strip()
            amount_cents = parse_amount(row["amount"])
            datetime.fromisoformat(date.replace("Z", "+00:00"))
            if not (reference and bank_ref and len(reference) <= 80 and len(bank_ref) <= 80 and currency in {"USD", "EUR", "GBP"}):
                raise ValueError("Invalid reference or currency")
            parsed.append((reference, amount_cents, currency, date, bank_ref))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Row {number}: {exc}") from exc
    if not parsed:
        raise ValueError("CSV contains no settlement records")
    if len(parsed) > 200:
        raise ValueError("Import limit: 200 settlements per file")
    if len({row[0] for row in parsed}) != len(parsed):
        raise ValueError("CSV contains duplicate gateway_reference values")
    return parsed


def safe_cell(value: object) -> str:
    """Protect spreadsheet users from CSV formula injection."""
    string = "" if value is None else str(value)
    return "'" + string if string.lstrip().startswith(("=", "+", "-", "@")) else string


def create_app(db_path: str | Path | None = None) -> FastAPI:
    target = Path(db_path) if db_path is not None else DEFAULT_DB
    initialize(target)
    app = FastAPI(title="ReconPilot API", description="Synthetic payment reconciliation and risk operations demo", version="1.0.0")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "reconpilot", "data_mode": "synthetic"}

    @app.get("/api/summary")
    def summary():
        with connect(target) as db:
            return get_summary(list_joined(db))

    @app.get("/api/transactions")
    def transactions(
        status: Annotated[str, Query()] = "all",
        q: Annotated[str, Query(max_length=100)] = "",
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if status not in STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status filter")
        with connect(target) as db:
            items = list_joined(db)
        if status == "exceptions":
            items = [x for x in items if x["is_exception"]]
        elif status != "all":
            items = [x for x in items if x["status"] == status]
        if q.strip():
            needle = q.lower().strip()
            items = [x for x in items if needle in x["id"].lower() or needle in x["merchant"].lower() or needle in (x["bank_reference"] or "").lower()]
        return {"total": len(items), "items": items[offset : offset + limit], "limit": limit, "offset": offset}

    @app.get("/api/transactions/{payment_id}")
    def transaction_detail(payment_id: str):
        with connect(target) as db:
            item = next((x for x in list_joined(db) if x["id"] == payment_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Payment not found")
        return item

    @app.post("/api/transactions/{payment_id}/review")
    def review(payment_id: str, change: ReviewChange):
        with connect(target) as db:
            existing = db.execute("SELECT reviewed FROM payments WHERE id = ?", (payment_id,)).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Payment not found")
            if bool(existing[0]) != change.reviewed:
                db.execute("UPDATE payments SET reviewed = ? WHERE id = ?", (int(change.reviewed), payment_id))
                db.execute("INSERT INTO audit_events(created_at, actor, action, details) VALUES (?, ?, ?, ?)",
                           (now_utc().isoformat(), "demo-analyst", "REVIEW_UPDATED", f"{payment_id}: reviewed={change.reviewed}"))
        return {"payment_id": payment_id, "reviewed": change.reviewed}

    @app.get("/api/audit")
    def audit(limit: Annotated[int, Query(ge=1, le=100)] = 30):
        with connect(target) as db:
            rows = db.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {"items": [dict(r) for r in rows]}

    @app.post("/api/settlements/import")
    async def import_settlements(request: Request):
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != "text/csv":
            raise HTTPException(status_code=415, detail="Send CSV as text/csv")
        payload = await request.body()
        if len(payload) > 100_000:
            raise HTTPException(status_code=413, detail="CSV file must be <= 100 KB")
        try:
            rows = parse_settlement_csv(payload.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        inserted = 0
        skipped = 0
        with connect(target) as db:
            # Validate all references and conflicts before any inserts: all-or-nothing.
            for ref, amount, currency, date, bank_ref in rows:
                if db.execute("SELECT 1 FROM payments WHERE id = ?", (ref,)).fetchone() is None:
                    raise HTTPException(status_code=422, detail=f"Unknown payment reference: {ref}")
                existing = db.execute("SELECT amount_cents, currency, settled_at, bank_reference FROM settlements WHERE gateway_reference = ?", (ref,)).fetchone()
                if existing and tuple(existing) != (amount, currency, date, bank_ref):
                    raise HTTPException(status_code=409, detail=f"Conflicting settlement already exists for: {ref}")
            for row in rows:
                result = db.execute("INSERT OR IGNORE INTO settlements(gateway_reference, amount_cents, currency, settled_at, bank_reference) VALUES (?, ?, ?, ?, ?)", row)
                inserted += result.rowcount
                skipped += 1 - result.rowcount
            db.execute("INSERT INTO audit_events(created_at, actor, action, details) VALUES (?, ?, ?, ?)",
                       (now_utc().isoformat(), "demo-analyst", "SETTLEMENT_CSV_IMPORTED", f"{inserted} imported, {skipped} duplicates skipped"))
        return {"inserted": inserted, "skipped_duplicates": skipped, "message": "Import completed"}

    @app.get("/api/export/transactions.csv")
    def export_csv():
        with connect(target) as db:
            items = list_joined(db)
        out = StringIO()
        writer = csv.writer(out)
        fields = ["id", "merchant", "amount_cents", "currency", "created_at", "payment_status", "status", "risk", "reviewed", "bank_reference", "settlement_amount_cents"]
        writer.writerow(fields)
        for item in items:
            writer.writerow([safe_cell(item.get(k)) for k in fields])
        return Response(content=out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="reconpilot-transactions.csv"'})

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/styles.css", include_in_schema=False)
    def styles():
        return FileResponse(STATIC / "styles.css", media_type="text/css")

    @app.get("/app.js", include_in_schema=False)
    def client():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    return app


app = create_app()
