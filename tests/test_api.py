"""Tests for reconciliation, idempotent imports, review logs and safe exports."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app, safe_cell, parse_amount


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(tmp_path / "test.db")
    with TestClient(app) as client:
        yield client


def test_seeded_dashboard_and_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data_mode"] == "synthetic"
    result = client.get("/api/summary").json()
    assert result["totals"]["transactions"] == 420
    assert 0 < result["totals"]["match_rate_pct"] < 100
    assert result["totals"]["open_exceptions"] > 0
    assert len(result["daily_volume"]) == 14
    assert len(result["merchants"]) == 6


def test_filters_pagination_and_bad_input(client):
    items = client.get("/api/transactions?status=exceptions&limit=6").json()
    assert len(items["items"]) == 6
    assert all(x["is_exception"] for x in items["items"])
    needle = items["items"][0]["id"]
    found = client.get("/api/transactions", params={"q": needle}).json()
    assert found["total"] == 1
    assert found["items"][0]["id"] == needle
    assert client.get("/api/transactions?status=unknown").status_code == 400
    assert client.get("/api/transactions?limit=501").status_code == 422
    assert client.get("/api/transactions?offset=-1").status_code == 422
    assert client.get("/api/transactions/NOT-REAL").status_code == 404


def test_review_is_audited_and_idempotent(client):
    before = client.get("/api/summary").json()["totals"]["open_exceptions"]
    item = client.get("/api/transactions?status=exceptions&limit=1").json()["items"][0]
    route = f"/api/transactions/{item['id']}/review"
    assert client.post(route, json={"reviewed": True}).json()["reviewed"] is True
    assert client.get("/api/summary").json()["totals"]["open_exceptions"] == before - 1
    first_events = client.get("/api/audit").json()["items"]
    assert first_events[0]["action"] == "REVIEW_UPDATED"
    assert client.post(route, json={"reviewed": True}).status_code == 200
    assert client.get("/api/audit").json()["items"] == first_events
    assert client.post(route, json={"reviewed": False}).json()["reviewed"] is False
    assert client.get("/api/summary").json()["totals"]["open_exceptions"] == before


def test_csv_import_reconciles_missing_and_is_idempotent(client):
    item = client.get("/api/transactions?status=missing_settlement&limit=1").json()["items"][0]
    dollars = f"{item['amount_cents'] / 100:.2f}"
    payload = f"gateway_reference,amount,currency,settlement_date,bank_reference\n{item['id']},{dollars},USD,2026-09-15,TEST-BNK-1\n"
    url = "/api/settlements/import"
    first = client.post(url, content=payload, headers={"Content-Type": "text/csv"})
    assert first.status_code == 200, first.text
    assert first.json()["inserted"] == 1
    assert client.get(f"/api/transactions/{item['id']}").json()["status"] == "matched"
    second = client.post(url, content=payload, headers={"Content-Type": "text/csv"})
    assert second.json()["skipped_duplicates"] == 1
    assert second.json()["inserted"] == 0


def test_bad_import_is_rejected_without_mutation(client):
    before = client.get("/api/summary").json()["status_counts"]
    payload = "gateway_reference,amount,currency,settlement_date,bank_reference\nX-1,10.001,USD,2026-10-01,BNK\n"
    result = client.post("/api/settlements/import", content=payload, headers={"Content-Type": "text/csv"})
    assert result.status_code == 422
    assert "Row 2" in result.json()["detail"]
    assert client.get("/api/summary").json()["status_counts"] == before
    assert client.post("/api/settlements/import", content=payload).status_code == 415
    assert parse_amount("12.30") == 1230
    with pytest.raises(ValueError):
        parse_amount("0")


def test_export_content_and_formula_sanitization(client):
    result = client.get("/api/export/transactions.csv")
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/csv")
    assert len(result.text.strip().splitlines()) == 421
    assert safe_cell("=HYPERLINK(\"bad\")").startswith("'=")
    assert safe_cell("@danger").startswith("'@")
    assert safe_cell("ATLAS") == "ATLAS"


def test_static_ui(client):
    home = client.get("/")
    assert home.status_code == 200
    assert "ReconPilot" in home.text
    assert client.get("/styles.css").status_code == 200
    assert client.get("/app.js").status_code == 200


def test_unknown_and_conflicting_settlements(client):
    url = "/api/settlements/import"
    unknown = "gateway_reference,amount,currency,settlement_date,bank_reference\nNOT-EXIST,12.50,USD,2026-10-01,BNK-A\n"
    response = client.post(url, content=unknown, headers={"Content-Type": "text/csv"})
    assert response.status_code == 422
    assert "Unknown payment" in response.json()["detail"]

    item = client.get("/api/transactions?status=missing_settlement&limit=1").json()["items"][0]
    first = f"gateway_reference,amount,currency,settlement_date,bank_reference\n{item['id']},12.50,USD,2026-10-01,BNK-A\n"
    assert client.post(url, content=first, headers={"Content-Type": "text/csv"}).status_code == 200
    conflicting = first.replace("12.50", "13.50")
    response = client.post(url, content=conflicting, headers={"Content-Type": "text/csv"})
    assert response.status_code == 409
    assert client.get(f"/api/transactions/{item['id']}").json()["settlement_amount_cents"] == 1250
