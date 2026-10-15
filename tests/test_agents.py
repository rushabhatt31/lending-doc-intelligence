from docintel.agents.bank_statement import categorise, parse_text
from docintel.agents.financial_analysis import compute_ratios
from docintel.agents.fraud_detection import fuse
from docintel.ingest import load_bytes, load_path
from docintel.agents import DocumentIntelligenceAgent
from docintel.models import RiskSignal, Severity

from conftest import SAMPLES

DI = DocumentIntelligenceAgent()


def classify(path):
    d = load_path(path)
    DI.process_document(d)
    return d


def test_classifies_every_sample_correctly():
    expected = {
        "pan_card.txt": "pan_card", "aadhaar.txt": "aadhaar", "payslip_aug_2026.txt": "salary_slip",
        "form16_fy2025-26.txt": "form_16", "epf_passbook.txt": "epfo_passbook",
        "bank_statement_apr-sep_2026.csv": "bank_statement", "gst_certificate.txt": "gst_certificate",
        "gst_returns.json": "gst_returns_data", "financials_fy24-26.json": "financial_statements",
        "bank_statement_jul-sep_2026.txt": "bank_statement",
    }
    for f in SAMPLES.rglob("*"):
        if f.name in expected:
            assert classify(f).doc_type == expected[f.name], f.name


def test_extraction_masks_aadhaar_and_validates_pan():
    d = classify(SAMPLES / "retail_clean" / "aadhaar.txt")
    assert "aadhaar" not in d.fields and d.fields["aadhaar_masked"].startswith("XXXX XXXX")
    assert d.fields["aadhaar_checksum_valid"] is True
    p = classify(SAMPLES / "retail_clean" / "payslip_sep_2026.txt")
    assert p.fields["net_salary"] == 92400 and p.fields["gross_salary"] == 115000
    assert p.fields["pan_valid"] is True


def test_editing_software_is_flagged():
    d = load_bytes("statement.txt", b"Statement of Account\nOpening Balance\nClosing Balance\nWithdrawal Deposit")
    d.metadata["producer"] = "Adobe Photoshop 25.0"
    DI.process_document(d)
    sigs = DI.forensics(d)
    assert any(s.code == "DOC_EDITING_SOFTWARE" and s.severity == Severity.HIGH for s in sigs)


def test_unknown_document_is_unclassified():
    d = load_bytes("random.txt", b"Grocery list: eggs, milk, bread")
    DI.process_document(d)
    assert d.doc_type is None


def test_categoriser():
    assert categorise("NEFT CR-ACME TECHNOLOGIES-SALARY SEP") == "salary"
    assert categorise("NACH RTN-INSUFF FUNDS") == "bounce"
    assert categorise("NACH RTN CHG") == "bounce_charge"
    assert categorise("NACH DR-ZENITH FINANCE LTD-EMI") == "emi_loan"
    assert categorise("UPI/swiggy/food") == "upi"


def test_text_statement_parser_infers_direction_from_balance():
    text = """Opening Balance: 1,000.00
01/07/2026  SALARY CREDIT  5,000.00  6,000.00
02/07/2026  ATM CASH WDL   2,000.00  4,000.00"""
    txns, opening = parse_text(text)
    assert opening == 1000
    assert txns[0]["credit"] == 5000 and txns[1]["debit"] == 2000


def test_ratios():
    r = compute_ratios(dict(revenue=1000, cogs=600, employee_cost=100, other_expenses=100, depreciation=50,
                            interest=50, tax=30, current_assets=500, current_liabilities=250, net_worth=400,
                            long_term_debt=200, short_term_debt=100, total_assets=1000, receivables=100))
    assert r["ebitda"] == 200 and r["ebitda_margin_pct"] == 20
    assert r["current_ratio"] == 2 and r["debt_equity"] == 0.75
    assert r["interest_coverage"] == 3
    assert r["receivable_days"] == 36.5


def test_fusion_is_monotonic_and_bounded():
    s = lambda sev: RiskSignal(code="X", message="", severity=sev, source_agent="t")  # noqa: E731
    assert fuse([])[0] == 0
    one, _ = fuse([s(Severity.HIGH)])
    two, _ = fuse([s(Severity.HIGH), s(Severity.HIGH)])
    assert 0 < one < two < 100
    assert fuse([s(Severity.CRITICAL)])[1] in ("high", "critical")
