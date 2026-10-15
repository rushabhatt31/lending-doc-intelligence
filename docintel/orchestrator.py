"""Orchestrator: runs the agent pipeline for a loan application.

Upload -> Document Intelligence (immediate) -> Bank Statement -> Income (retail)
/ GST + Financials (SME) -> Fraud fusion -> pendency queries -> recommendation.
The system recommends; a credit officer decides.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agents.bank_statement import BankStatementAgent
from .agents.base import AgentContext, Config
from .agents.document_intelligence import DocumentIntelligenceAgent
from .agents.financial_analysis import FinancialAnalysisAgent
from .agents.fraud_detection import FraudDetectionAgent, IdentityRegistry
from .agents.gst_analytics import GSTAnalyticsAgent
from .agents.income_verification import IncomeVerificationAgent
from .audit import AuditLog
from .connectors import Connectors
from .ingest import load_bytes
from .llm import LLM
from .models import Applicant, LoanApplication, QueryStatus, Segment, Severity
from .pendency import PendencyManager

PIPELINE = [
    DocumentIntelligenceAgent(),
    BankStatementAgent(),
    IncomeVerificationAgent(),
    FinancialAnalysisAgent(),
    GSTAnalyticsAgent(),  # after bank so it can cross-check GST turnover vs credits
    FraudDetectionAgent(),  # always last: fuses everyone's signals
]

# Credit-officer question -> where the answer lives
QA_ROUTES: list[tuple[tuple[str, ...], str, list[str]]] = [
    (("abb", "average bank balance", "balance"), "bank_statement_analyser", ["abb", "abb_monthly", "avg_monthly_credits"]),
    (("bounce", "return", "nach"), "bank_statement_analyser", ["bounce_count", "emi_bounce_count"]),
    (("emi", "obligation"), "bank_statement_analyser", ["emi_obligations", "total_monthly_emi_obligation"]),
    (("foir", "afford", "eligib", "max loan"), "income_verification", ["foir_pct", "max_eligible_emi", "max_eligible_loan"]),
    (("income", "salary", "employer", "epfo"), "income_verification",
     ["verified_monthly_income", "verification_basis", "income_sources", "employer_match"]),
    (("gst", "gstr", "compliance", "anchor", "buyer"), "gst_analytics",
     ["compliance_score", "annual_variance_pct", "late_filings", "top_buyer_share_pct", "sector_risk"]),
    (("dscr", "ratio", "leverage", "debt", "margin", "revenue", "benchmark"), "financial_analysis", ["benchmark", "trends"]),
    (("fraud", "risk", "tamper", "duplicate", "synthetic"), "fraud_detection", ["risk_score", "risk_band", "top_reasons"]),
    (("missing", "document", "checklist"), "document_intelligence", ["missing_document_groups", "checklist_complete"]),
]


class LendingOrchestrator:
    def __init__(self, audit_path: str | Path | None = None, connectors: Connectors | None = None,
                 config: Config | None = None, llm: LLM | None = None):
        self.audit = AuditLog(audit_path)
        self.pendency = PendencyManager(self.audit)
        self.connectors = connectors or Connectors()
        self.config = config or Config()
        self.llm = llm or LLM()
        self.registry = IdentityRegistry()
        self.applications: dict[str, LoanApplication] = {}
        self._di = DocumentIntelligenceAgent()

    def _ctx(self, app: LoanApplication) -> AgentContext:
        return AgentContext(app=app, audit=self.audit, llm=self.llm, connectors=self.connectors,
                            identity_registry=self.registry, config=self.config)

    # ---------- lifecycle ----------
    def create_application(self, segment: str | Segment, applicant: dict | Applicant, product: str = "personal_loan") -> LoanApplication:
        app = LoanApplication(segment=Segment(segment), product=product,
                              applicant=applicant if isinstance(applicant, Applicant) else Applicant(**applicant))
        self.applications[app.id] = app
        self.audit.record(app.id, "system", "application_created",
                          {"segment": app.segment.value, "product": product, "applicant": app.applicant.name})
        return app

    def add_document(self, app_id: str, filename: str, content: bytes):
        app = self.applications[app_id]
        doc = load_bytes(filename, content)
        app.documents.append(doc)
        self._di.process_document(doc, self._ctx(app))
        self.audit.record(app_id, "document_intelligence", "document_ingested", {
            "document_id": doc.id, "filename": filename, "sha256": doc.sha256, "doc_type": doc.doc_type,
            "confidence": doc.classification_confidence, "fields_extracted": sorted(doc.fields),
        })
        if doc.doc_type:
            self.pendency.auto_resolve_on_document(app_id, doc.doc_type, doc.id)
        return doc

    def add_document_path(self, app_id: str, path: str | Path):
        p = Path(path)
        return self.add_document(app_id, p.name, p.read_bytes())

    def process(self, app_id: str) -> LoanApplication:
        app = self.applications[app_id]
        ctx = self._ctx(app)
        app.results = {}
        self.audit.record(app_id, "system", "pipeline_started", {"documents": len(app.documents)})
        raised_now: set[str] = set()
        for agent in PIPELINE:
            result = agent.run(ctx)
            app.results[agent.name] = result
            for item in result.open_items:
                q = self.pendency.raise_query(app_id, agent.name, item)
                raised_now.add(q.id)

        # Issues an agent no longer reports (fixed by new data) close themselves
        for q in self.pendency.for_application(app_id, open_only=True):
            if q.id not in raised_now and q.raised_by in app.results:
                self.pendency.resolve(q.id, "system", "No longer raised after re-run")

        self.registry.register(app)
        app.decision = self._recommend(app)
        self.audit.record(app_id, "system", "recommendation", app.decision)
        return app

    def _recommend(self, app: LoanApplication) -> dict[str, Any]:
        fraud = app.results.get("fraud_detection")
        band = fraud.output.get("risk_band", "low") if fraud and fraud.status == "ok" else "unknown"
        signals = [s for r in app.results.values() for s in r.signals]
        highs = [s for s in signals if s.severity in (Severity.HIGH, Severity.CRITICAL)]
        mediums = [s for s in signals if s.severity == Severity.MEDIUM]
        open_q = self.pendency.for_application(app.id, open_only=True)
        blocking = [q for q in open_q if q.blocking]

        if band in ("critical", "high"):
            rec = "REFER_FRAUD_REVIEW"
        elif blocking:
            rec = "PENDING_INFORMATION"
        elif highs:
            rec = "REFER_TO_CREDIT"
        elif band == "medium" or mediums:
            rec = "PROCEED_WITH_CONDITIONS"
        else:
            rec = "STP_ELIGIBLE"

        key_metrics = {}
        r = app.results
        if (b := r.get("bank_statement_analyser")) and b.status == "ok":
            key_metrics.update(abb=b.output["abb"], bounces=b.output["bounce_count"],
                               bank_emi_obligation=b.output["total_monthly_emi_obligation"])
        if (i := r.get("income_verification")) and i.status == "ok":
            key_metrics.update(verified_income=i.output["verified_monthly_income"], foir_pct=i.output["foir_pct"],
                               max_eligible_loan=i.output["max_eligible_loan"])
        if (g := r.get("gst_analytics")) and g.status == "ok":
            key_metrics.update(gst_compliance=g.output["compliance_score"], gst_variance_pct=g.output["annual_variance_pct"])
        if (f := r.get("financial_analysis")) and f.status == "ok":
            latest = f.output["ratios"][f.output["years"][-1]]
            key_metrics.update(dscr=latest.get("dscr"), debt_equity=latest.get("debt_equity"),
                               revenue=latest.get("revenue"))
        if fraud and fraud.status == "ok":
            key_metrics.update(fraud_score=fraud.output["risk_score"], fraud_band=band)

        return {
            "recommendation": rec,
            "requires_human_decision": rec != "STP_ELIGIBLE",
            "key_metrics": key_metrics,
            "high_severity_signals": [f"{s.code}: {s.message}" for s in highs],
            "conditions": [f"{s.code}: {s.message}" for s in mediums],
            "open_queries": len(open_q), "blocking_queries": len(blocking),
        }

    # ---------- credit officer support ----------
    def ask(self, app_id: str, question: str, actor: str = "credit_officer") -> dict[str, Any]:
        """Answer a credit officer's question from the evidence, with citations to agent outputs."""
        app = self.applications[app_id]
        q = question.lower()
        evidence: dict[str, Any] = {}
        for words, agent, keys in QA_ROUTES:
            if any(w in q for w in words) and (res := app.results.get(agent)) and res.status == "ok":
                evidence[agent] = {k: res.output.get(k) for k in keys}
                evidence[agent]["summary"] = res.summary
        if any(w in q for w in ("pending", "open", "query", "queries", "waiting")):
            evidence["pendency"] = [{"id": x.id, "assignee": x.assignee, "question": x.question, "blocking": x.blocking}
                                    for x in self.pendency.for_application(app_id, open_only=True)]

        answer = None
        if self.llm.available:
            payload = evidence or {k: v.summary for k, v in app.results.items()}
            ans = self.llm.extract_json(
                f"You support a credit officer. Answer their question using ONLY this evidence; cite the agent "
                f"names; say 'not available' if the evidence doesn't cover it. Question: {question}. "
                'Return {"answer": str}.', json.dumps(payload, default=str))
            answer = ans.get("answer") if ans else None
        if answer is None:
            parts = []
            for agent, ev in evidence.items():
                if agent == "pendency":
                    continue
                parts.append(f"{ev['summary']} [{agent}]")
                for reason in (ev.get("top_reasons") or [])[:3]:
                    parts.append(f"  - {reason}")
            if "pendency" in evidence:
                items = evidence["pendency"]
                parts.append(f"{len(items)} open quer{'y' if len(items) == 1 else 'ies'}" + (":" if items else "."))
                parts += [f"  - [{x['assignee']}] {x['question']}" for x in items]
            answer = "\n".join(parts) or "No matching evidence. Try: ABB, bounces, FOIR, income, GST, DSCR, fraud, pending."
        self.audit.record(app_id, actor, "officer_question", {"question": question, "answered_from": sorted(evidence)})
        return {"question": question, "answer": answer, "evidence": evidence}

    def report(self, app_id: str) -> dict[str, Any]:
        app = self.applications[app_id]
        return {
            "application_id": app.id, "segment": app.segment.value, "applicant": app.applicant.name,
            "decision": app.decision,
            "agents": {k: {"status": v.status, "summary": v.summary, "signals": [s.model_dump(mode="json") for s in v.signals],
                           "duration_ms": v.duration_ms} for k, v in app.results.items()},
            "documents": [{"filename": d.filename, "doc_type": d.doc_type, "confidence": d.classification_confidence}
                          for d in app.documents],
            "pendency": [q.model_dump(mode="json") for q in self.pendency.for_application(app_id)],
            "pendency_sla": self.pendency.sla_report(app_id),
            "audit_entries": len(self.audit.trail(app_id)),
        }


__all__ = ["LendingOrchestrator", "QueryStatus"]
