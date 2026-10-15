from fastapi.testclient import TestClient

from docintel.api import app
from conftest import SAMPLES

client = TestClient(app)


def test_end_to_end_over_http():
    assert client.get("/health").json()["status"] == "ok"
    r = client.post("/applications", json={"segment": "sme", "product": "working_capital", "applicant": {
        "name": "Ganesh Auto Components", "pan": "AAHFG1234Q", "sector": "manufacturing"}})
    assert r.status_code == 201
    app_id = r.json()["application_id"]

    files = [("files", (f.name, f.read_bytes())) for f in sorted((SAMPLES / "sme").iterdir())]
    up = client.post(f"/applications/{app_id}/documents", files=files)
    assert up.status_code == 201
    assert {d["doc_type"] for d in up.json()["documents"]} >= {"gst_returns_data", "financial_statements", "bank_statement"}

    rep = client.post(f"/applications/{app_id}/process").json()
    assert rep["decision"]["recommendation"] == "PROCEED_WITH_CONDITIONS"
    assert client.get(f"/applications/{app_id}/results/gst_analytics").json()["output"]["compliance_score"] > 70

    ans = client.post(f"/applications/{app_id}/ask", json={"question": "What is the DSCR?"}).json()
    assert "financial_analysis" in ans["evidence"]
    assert client.get("/audit/verify").json()["valid"]


def test_unknown_application_404():
    assert client.get("/applications/app_nope").status_code == 404
