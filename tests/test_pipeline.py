from docintel.models import QueryStatus


def test_clean_retail_is_straight_through(run):
    app = run("retail_clean")
    d = app.decision
    assert d["recommendation"] == "STP_ELIGIBLE"
    m = d["key_metrics"]
    assert m["verified_income"] == 92400
    assert 30 < m["foir_pct"] < 45
    assert m["bounces"] == 0 and m["fraud_band"] == "low"
    bank = app.results["bank_statement_analyser"].output
    assert bank["period"]["months"] == 6 and bank["total_monthly_emi_obligation"] == 12500
    assert not app.results["bank_statement_analyser"].signals  # balances are continuous


def test_risky_retail_goes_to_fraud_review(run):
    run("retail_clean")  # earlier application whose payslip gets reused
    app = run("retail_risky")
    codes = {s.code for r in app.results.values() for s in r.signals}
    for expected in ["STATEMENT_BALANCE_MISMATCH", "AADHAAR_CHECKSUM_FAIL", "DUPLICATE_DOCUMENT", "DOB_MISMATCH",
                     "NAME_MISMATCH", "SHARED_MOBILE", "EMI_BOUNCE", "FOIR_EXCEEDS_CAP", "SYNTHETIC_ID_SUSPECTED"]:
        assert expected in codes, expected
    assert app.decision["recommendation"] == "REFER_FRAUD_REVIEW"


def test_sme_credit_signals_do_not_inflate_fraud_score(run):
    app = run("sme")
    assert app.results["fraud_detection"].output["risk_band"] == "low"
    gst = app.results["gst_analytics"].output
    assert gst["compliance_band"] in ("A", "B") and len(gst["late_filings"]) == 1
    assert any(s.code == "ANCHOR_CONCENTRATION" for s in app.results["gst_analytics"].signals)
    fin = app.results["financial_analysis"].output
    assert fin["ratio_count"] >= 25 and fin["years"] == ["FY2024", "FY2025", "FY2026"]
    assert app.decision["recommendation"] == "PROCEED_WITH_CONDITIONS"


def test_missing_documents_raise_queries_that_autoresolve(orch):
    from conftest import SAMPLES

    app = orch.create_application("retail", {"name": "Rahul Sharma", "pan": "ABCPS1234K"})
    orch.add_document_path(app.id, SAMPLES / "retail_clean" / "pan_card.txt")
    orch.process(app.id)
    assert app.decision["recommendation"] == "PENDING_INFORMATION"
    open_q = orch.pendency.for_application(app.id, open_only=True)
    bank_q = next(q for q in open_q if q.required_doc_type == "bank_statement")

    orch.add_document_path(app.id, SAMPLES / "retail_clean" / "bank_statement_apr-sep_2026.csv")
    assert orch.pendency.get(bank_q.id).status == QueryStatus.RESOLVED

    orch.add_document_path(app.id, SAMPLES / "retail_clean" / "aadhaar.txt")
    orch.add_document_path(app.id, SAMPLES / "retail_clean" / "payslip_sep_2026.txt")
    orch.process(app.id)
    assert app.results["document_intelligence"].output["checklist_complete"]


def test_officer_query_lifecycle_and_audit_chain(orch, run):
    app = run("retail_risky")
    q = orch.pendency.for_application(app.id, open_only=True)[0]
    orch.pendency.respond(q.id, "customer", "Uploading more statements")
    orch.pendency.resolve(q.id, "officer_17", "Received", waive=False)
    assert orch.pendency.get(q.id).status == QueryStatus.RESOLVED
    actions = [e["action"] for e in orch.audit.trail(app.id)]
    assert {"document_ingested", "agent_completed", "query_raised", "query_responded", "query_resolved"} <= set(actions)
    assert orch.audit.verify()["valid"]


def test_audit_tampering_is_detected(orch, run):
    run("retail_clean")
    orch.audit._entries[3]["details"]["doc_type"] = "forged"
    assert orch.audit.verify() == {"valid": False, "broken_at_seq": 3}


def test_officer_qa_cites_evidence(orch, run):
    app = run("retail_clean")
    ans = orch.ask(app.id, "What is the ABB and FOIR?")
    assert "bank_statement_analyser" in ans["evidence"] and "income_verification" in ans["evidence"]
    assert ans["evidence"]["income_verification"]["foir_pct"] == app.decision["key_metrics"]["foir_pct"]
