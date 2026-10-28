"""Demo workflow: import a bank settlement to resolve a missing payment.

Start the API first: python -m app
Then run:        python scripts/reconcile_example.py
No non-standard dependencies are required for this helper.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import json
import os
from urllib.request import Request, urlopen

BASE = os.getenv("RECONPILOT_URL", "http://127.0.0.1:8000").rstrip("/")


def get(path: str) -> dict:
    with urlopen(BASE + path, timeout=10) as result:
        return json.load(result)


def main() -> None:
    missing = get("/api/transactions?status=missing_settlement&limit=1")["items"]
    if not missing:
        print("No missing settlement records remain.")
        return
    payment = missing[0]
    amount = Decimal(payment["amount_cents"]) / 100
    csv_data = StringIO()
    writer = csv.writer(csv_data)
    writer.writerow(["gateway_reference", "amount", "currency", "settlement_date", "bank_reference"])
    writer.writerow([payment["id"], f"{amount:.2f}", payment["currency"], datetime.now(timezone.utc).date().isoformat(), "DEMO-BANK-IMPORT"])
    request = Request(BASE + "/api/settlements/import", data=csv_data.getvalue().encode("utf-8"), headers={"Content-Type": "text/csv"}, method="POST")
    with urlopen(request, timeout=10) as result:
        print("Import:", json.load(result))
    updated = get("/api/transactions/" + payment["id"])
    print(f"Payment {payment['id']}: {payment['status']} -> {updated['status']}")


if __name__ == "__main__":
    main()
