"""The dashboard API surface (docs/06 §2)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api, cases, executor, webhooks
from tests.conftest import failure_payload, recovery_payload


@pytest.fixture()
def client(fresh_db, sim_clock):
    app = FastAPI()
    app.include_router(api.router)
    webhooks.intake(failure_payload(event_id="e1", sub_id="s1", cust_id="c1"), source="synthetic")
    webhooks.intake(failure_payload(event_id="e2", sub_id="s2", cust_id="c2",
                                    error=None), source="synthetic")
    # A card_expired case: the link goes out, and the case stays open awaiting payment.
    webhooks.intake(failure_payload(
        event_id="e4", sub_id="s3", cust_id="c3",
        error=("BAD_REQUEST_ERROR", "card_expired", "The card has expired", "customer")),
        source="synthetic")
    sim_clock.advance(hours=25)
    executor.tick()
    case = cases.find_latest_by_subscription("s1")
    webhooks.intake(recovery_payload(event_id="e3", sub_id="s1", cust_id="c1",
                                     case_id=case["id"]), source="synthetic")
    return TestClient(app)


def test_summary_exposes_the_synthetic_split_and_the_bounds(client):
    s = client.get("/api/summary").json()
    assert s["n_cases"] == 3 and s["n_synthetic"] == 3
    assert s["synthetic_share"] == 1.0
    assert s["bounds"]["max_attempts"] == 3
    assert s["reconciliation"]["counts_balance"]


def test_categories_endpoint(client):
    rows = client.get("/api/categories").json()
    assert {r["category"] for r in rows} == {"insufficient_funds", "unknown", "card_expired"}


def test_case_list_filters(client):
    assert len(client.get("/api/cases").json()) == 3
    assert len(client.get("/api/cases?status=recovered").json()) == 1
    assert len(client.get("/api/cases?category=unknown").json()) == 1
    assert client.get("/api/cases?status=nonsense").json() == []


def test_case_file_contains_the_whole_story(client):
    case_id = client.get("/api/cases?status=recovered").json()[0]["case_id"]
    d = client.get(f"/api/cases/{case_id}").json()
    assert d["case"]["status"] == "recovered"
    assert d["events"] and d["diagnoses"] and d["decisions"]
    assert d["audit_trail"][0]["seq"] == 1
    assert d["audit_trail"][-1]["stage"] == "outcome"
    assert isinstance(d["decisions"][0]["invariant_check"], dict)


def test_unknown_case_is_404(client):
    assert client.get("/api/cases/case_does_not_exist").status_code == 404


def test_trace_endpoint_round_trips_to_case_files(client):
    traced = client.get("/api/metrics/trace/recovered").json()
    assert traced["n_cases"] == len(traced["case_ids"]) == 1
    for case_id in traced["case_ids"]:
        d = client.get(f"/api/cases/{case_id}").json()
        assert any(e["stage"] == "outcome" for e in d["audit_trail"])
    assert client.get("/api/metrics/trace/made_up").status_code == 404


def test_opt_out_helper_marks_the_customer_and_audits_it(client):
    open_cases = [c for c in client.get("/api/cases").json() if c["status"] == "open"]
    assert open_cases, "fixture must leave an open case for the I3 demo helper"
    case_id = open_cases[0]["case_id"]
    assert client.post(f"/api/cases/{case_id}/opt-out").json()["customer_opted_out"] is True
    d = client.get(f"/api/cases/{case_id}").json()
    assert d["case"]["customer_opted_out"] == 1
    assert any(e["actor"] == "human" for e in d["audit_trail"])
