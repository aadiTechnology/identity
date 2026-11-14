"""The published contract of Identity: the OpenAPI document must still offer every endpoint listed in contracts/identity-v1-paths.json."""
import json
from pathlib import Path

from app import service

CONTRACT = json.loads((Path(__file__).resolve().parents[1] / "contracts" / "identity-v1-paths.json").read_text(encoding="utf-8"))


def test_every_contracted_endpoint_is_offered(client):
    doc = client.get("/api/v1/openapi.json").json()
    missing = [(m, p) for m, p in CONTRACT["paths"] if m.lower() not in doc["paths"].get(p, {})]
    assert not missing, f"breaking change: endpoints removed from the contract: {missing}"


def test_the_contract_document_is_served_and_versioned(client):
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200 and r.json()["info"]["version"] == CONTRACT["version"]


def test_scopes_in_the_contract_match_the_code():
    assert set(CONTRACT["identity_scopes"]) == service.IDENTITY_SCOPES
    assert service.IDENTITY_AUDIENCE in CONTRACT["token_audiences"]


def test_the_contract_check_would_catch_a_removal(client):
    doc = client.get("/api/v1/openapi.json").json()
    doc["paths"].pop("/api/v1/products/enrol")
    assert [(m, p) for m, p in CONTRACT["paths"] if m.lower() not in doc["paths"].get(p, {})] == [("POST", "/api/v1/products/enrol")]
