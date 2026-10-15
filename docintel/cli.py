"""Command-line demo.

    python -m docintel.cli demo                 # run all three sample cases
    python -m docintel.cli run --segment retail --name "Rahul Sharma" --pan ABCPS1234K file1 file2 ...
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .orchestrator import LendingOrchestrator

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

CASES = [
    ("retail_clean", "retail", "personal_loan", dict(
        name="Rahul Sharma", pan="ABCPS1234K", mobile="9876543210", declared_monthly_income=95000,
        existing_emi=12500, requested_amount=800000, requested_tenure_months=48)),
    ("retail_risky", "retail", "personal_loan", dict(
        name="Karan Mehta", pan="BQRPK4821M", mobile="9876543210", declared_monthly_income=110000,
        requested_amount=1200000, requested_tenure_months=36)),
    ("sme", "sme", "working_capital", dict(
        name="Ganesh Auto Components", pan="AAHFG1234Q", business_name="Ganesh Auto Components",
        gstin="29AAHFG1234Q1Z0", sector="manufacturing", requested_amount=7500000, requested_tenure_months=60)),
]

C = {"STP_ELIGIBLE": "\033[92m", "PROCEED_WITH_CONDITIONS": "\033[92m", "REFER_TO_CREDIT": "\033[93m",
     "PENDING_INFORMATION": "\033[93m", "REFER_FRAUD_REVIEW": "\033[91m"}
END, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"


def print_report(orch: LendingOrchestrator, app_id: str):
    r = orch.report(app_id)
    d = r["decision"]
    print(f"\n{BOLD}{'═' * 78}\n {r['applicant']}  ·  {r['segment'].upper()}  ·  {app_id}{END}")
    print(f"{'═' * 78}")
    print(f"{DIM}Documents:{END}")
    for doc in r["documents"]:
        print(f"  • {doc['filename']:<38} → {doc['doc_type'] or '??':<22} {doc['confidence']:.0%}")
    print(f"{DIM}Agents:{END}")
    for name, a in r["agents"].items():
        mark = {"ok": "✓", "skipped": "–", "error": "✗"}[a["status"]]
        print(f"  {mark} {name:<24} {a['summary']}")
        for s in a["signals"]:
            print(f"      {DIM}[{s['severity']}] {s['code']}: {s['message']}{END}")
    open_q = [q for q in r["pendency"] if q["status"] in ("open", "responded")]
    if open_q:
        print(f"{DIM}Pendency ({len(open_q)} open):{END}")
        for q in open_q:
            print(f"  ? [{q['assignee']}{', blocking' if q['blocking'] else ''}] {q['question']}")
    print(f"{DIM}Key metrics:{END} " + json.dumps(d["key_metrics"], default=str))
    print(f"\n  Recommendation: {C.get(d['recommendation'], '')}{BOLD}{d['recommendation']}{END}"
          f"   (audit entries: {r['audit_entries']})")


def demo():
    orch = LendingOrchestrator()
    ids = {}
    for folder, segment, product, applicant in CASES:
        app = orch.create_application(segment, applicant, product)
        for f in sorted((SAMPLES / folder).iterdir()):
            orch.add_document_path(app.id, f)
        orch.process(app.id)
        ids[folder] = app.id
        print_report(orch, app.id)

    print(f"\n{BOLD}Credit officer Q&A{END}")
    for folder, q in [("retail_clean", "What is the FOIR and max eligible loan?"),
                      ("retail_risky", "Why is the fraud risk high?"),
                      ("sme", "How is GST compliance and are there pending queries?")]:
        ans = orch.ask(ids[folder], q)
        print(f"  Q ({folder}): {q}\n  A: {ans['answer']}\n")

    print(f"{BOLD}Audit chain:{END} {orch.audit.verify()}")
    return orch, ids


def main():
    p = argparse.ArgumentParser(prog="docintel")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo")
    run = sub.add_parser("run")
    run.add_argument("--segment", choices=["retail", "sme"], required=True)
    run.add_argument("--name", required=True)
    run.add_argument("--pan")
    run.add_argument("--amount", type=float)
    run.add_argument("--tenure", type=int, default=36)
    run.add_argument("--json", action="store_true", help="print full JSON report")
    run.add_argument("files", nargs="+")
    a = p.parse_args()
    if a.cmd == "demo":
        demo()
        return
    orch = LendingOrchestrator()
    app = orch.create_application(a.segment, dict(name=a.name, pan=a.pan, requested_amount=a.amount,
                                                  requested_tenure_months=a.tenure))
    for f in a.files:
        orch.add_document_path(app.id, f)
    orch.process(app.id)
    if a.json:
        print(json.dumps(orch.report(app.id), indent=2, default=str))
    else:
        print_report(orch, app.id)


if __name__ == "__main__":
    main()
