"""Fraud Detection Agent.

Runs last. Adds cross-application checks (duplicates, velocity, shared
identifiers -> fraud rings), identity consistency (synthetic-ID heuristics),
then fuses every agent's risk signals into one explainable score.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ..models import SEVERITY_WEIGHT, AgentResult, LoanApplication, RiskSignal, Severity, now
from ..utils import name_similarity, parse_date
from .base import AgentContext, BaseAgent

SEV_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]

# Signals from other agents that indicate deception, as opposed to weak credit quality.
# (Low DSCR or a bounce is a credit concern; an edited statement is a fraud concern.)
FRAUD_RELEVANT = {
    "DOC_EDITING_SOFTWARE", "DOC_MODIFIED_AFTER_CREATION", "DOC_FONT_INCONSISTENCY",
    "PAN_FORMAT_INVALID", "AADHAAR_CHECKSUM_FAIL", "GSTIN_CHECKSUM_FAIL", "GSTIN_INVALID", "GSTIN_PAN_MISMATCH",
    "SALARY_NET_GT_GROSS", "STATEMENT_BALANCE_MISMATCH", "WINDOW_DRESSING", "BANK_ACCOUNT_NAME_MISMATCH",
    "SALARY_SLIP_BANK_MISMATCH", "SALARY_FORM16_MISMATCH", "INCOME_OVERSTATED", "EMPLOYER_MISMATCH",
    "HIGH_ITC_UTILISATION", "BANK_CREDITS_ABOVE_GST", "BALANCE_SHEET_DOES_NOT_TALLY",
}


class IdentityRegistry:
    """Cross-application index of identifiers. Back with Redis/Postgres in production."""

    def __init__(self):
        self._index: dict[tuple[str, str], list[dict]] = defaultdict(list)

    @staticmethod
    def keys(app: LoanApplication) -> list[tuple[str, str]]:
        a = app.applicant
        ks = [("pan", a.pan), ("mobile", a.mobile), ("email", (a.email or "").lower() or None)]
        for d in app.documents:
            ks.append(("doc_hash", d.sha256))
            if d.doc_type in ("bank_statement", "cancelled_cheque") and d.fields.get("account_number"):
                ks.append(("bank_account", d.fields["account_number"]))
        return [(k, v) for k, v in ks if v]

    def register(self, app: LoanApplication) -> None:
        for k in self.keys(app):
            if not any(e["app_id"] == app.id for e in self._index[k]):
                self._index[k].append({"app_id": app.id, "pan": app.applicant.pan, "name": app.applicant.name, "ts": now()})

    def matches(self, app: LoanApplication, window_days: int) -> dict[str, list[dict]]:
        cutoff = now() - timedelta(days=window_days)
        out = {}
        for k in self.keys(app):
            hits = [e for e in self._index.get(k, []) if e["app_id"] != app.id and e["ts"] >= cutoff]
            if hits:
                out[f"{k[0]}:{k[1]}"] = hits
        return out


def fuse(signals: list[RiskSignal]) -> tuple[float, str]:
    """Noisy-OR fusion: independent signals compound, but the score saturates below 100."""
    remaining = 1.0
    for s in signals:
        remaining *= 1 - SEVERITY_WEIGHT[s.severity] / 100
    score = round((1 - remaining) * 100, 1)
    band = "low" if score < 20 else "medium" if score < 45 else "high" if score < 70 else "critical"
    if any(s.severity == Severity.CRITICAL for s in signals) and band in ("low", "medium"):
        band = "high"
    return score, band


class FraudDetectionAgent(BaseAgent):
    name = "fraud_detection"

    def _run(self, ctx: AgentContext) -> AgentResult:
        res = AgentResult(agent=self.name)
        app, cfg = ctx.app, ctx.config
        own: list[RiskSignal] = []

        # ---- cross-application: duplicates, velocity, rings ----
        matches = ctx.identity_registry.matches(app, cfg.velocity_window_days)
        for key, hits in matches.items():
            kind, value = key.split(":", 1)
            other_pans = {h["pan"] for h in hits if h["pan"] and h["pan"] != app.applicant.pan}
            if kind == "doc_hash":
                sev = Severity.CRITICAL if other_pans else Severity.HIGH
                doc = next(d for d in app.documents if d.sha256 == value)
                own.append(self.signal("DUPLICATE_DOCUMENT", f"'{doc.filename}' was already submitted in "
                                       f"{len(hits)} other application(s)", sev, doc, other_apps=[h["app_id"] for h in hits]))
            elif kind == "pan":
                sev = Severity.HIGH if len(hits) >= 2 else Severity.LOW
                own.append(self.signal("PAN_VELOCITY", f"PAN used in {len(hits)} other application(s) in "
                                       f"{cfg.velocity_window_days} days", sev, other_apps=[h["app_id"] for h in hits]))
            elif kind in ("mobile", "email", "bank_account") and other_pans:
                own.append(self.signal(f"SHARED_{kind.upper()}", f"{kind.replace('_', ' ')} shared with "
                                       f"{len(other_pans)} other identity(ies) — possible ring", Severity.HIGH,
                                       other_apps=[h["app_id"] for h in hits]))

        # ---- identity consistency ----
        names = {}
        for d in app.documents:
            if d.doc_type in ("pan_card", "aadhaar", "passport", "bank_statement", "salary_slip", "epfo_passbook") and d.fields.get("name"):
                names[d.doc_type] = d.fields["name"]
        name_scores = {k: name_similarity(app.applicant.name, n) for k, n in names.items()}
        for src, score in name_scores.items():
            if score < cfg.name_match_threshold:
                sev = Severity.HIGH if src in ("pan_card", "aadhaar", "passport") else Severity.MEDIUM
                own.append(self.signal("NAME_MISMATCH", f"Name on {src} ('{names[src]}') ≠ applicant "
                                       f"('{app.applicant.name}'), match {score:.0%}", sev))

        pans_on_docs = {d.fields["pan"] for d in app.documents
                        if d.fields.get("pan") and d.doc_type in ("pan_card", "form_16", "salary_slip")}
        if app.applicant.pan and pans_on_docs and app.applicant.pan not in pans_on_docs:
            own.append(self.signal("PAN_MISMATCH", f"Declared PAN {app.applicant.pan} not found on documents "
                                   f"({', '.join(sorted(pans_on_docs))})", Severity.HIGH))
        dobs = {d.doc_type: parse_date(d.fields["dob"]) for d in app.documents if d.fields.get("dob")}
        if len({x for x in dobs.values() if x}) > 1:
            own.append(self.signal("DOB_MISMATCH", f"Date of birth differs across documents: "
                                   f"{ {k: str(v_) for k, v_ in dobs.items()} }", Severity.HIGH))
        if app.segment.value == "retail" and app.applicant.pan and len(app.applicant.pan) == 10 and app.applicant.pan[3] != "P":
            own.append(self.signal("PAN_NOT_INDIVIDUAL", "Retail application with a non-individual PAN", Severity.MEDIUM))

        # ---- bureau soft pull (no score impact for the customer) ----
        bureau = {}
        if app.applicant.pan:
            bureau = ctx.connectors.bureau.soft_pull(app.applicant.pan, app.applicant.name, app.applicant.mobile)
            ctx.audit.record(app.id, self.name, "bureau_soft_pull", {"score": bureau.get("score")})
            if bureau.get("enquiries_last_6m", 0) >= 6:
                own.append(self.signal("CREDIT_HUNGRY", f"{bureau['enquiries_last_6m']} bureau enquiries in 6 months", Severity.MEDIUM))
            if bureau.get("dpd_90_last_12m", 0) > 0:
                own.append(self.signal("BUREAU_DPD90", "90+ DPD in last 12 months", Severity.HIGH))

        # ---- synthetic identity heuristic ----
        synthetic_markers = [s.code for s in own if s.code in
                             {"NAME_MISMATCH", "DOB_MISMATCH", "SHARED_MOBILE", "SHARED_EMAIL", "SHARED_BANK_ACCOUNT", "PAN_MISMATCH"}]
        prior = [s for r in app.results.values() for s in r.signals if s.code in FRAUD_RELEVANT]
        synthetic_markers += [s.code for s in prior if s.code in {"AADHAAR_CHECKSUM_FAIL", "PAN_FORMAT_INVALID"}]
        if bureau and bureau.get("active_loans", 1) == 0 and bureau.get("enquiries_last_6m", 0) >= 4:
            synthetic_markers.append("THIN_FILE_HIGH_ENQUIRY")
        if len(set(synthetic_markers)) >= 2:
            own.append(self.signal("SYNTHETIC_ID_SUSPECTED", "Multiple identity inconsistencies: " + ", ".join(sorted(set(synthetic_markers))),
                                   Severity.CRITICAL, markers=sorted(set(synthetic_markers))))

        # ---- fusion of fraud-relevant signals across all agents ----
        all_signals = prior + own
        score, band = fuse(all_signals)
        ranked = sorted(all_signals, key=lambda s: SEV_ORDER.index(s.severity))
        res.signals = own
        res.output = {
            "risk_score": score, "risk_band": band,
            "top_reasons": [f"[{s.severity.value}] {s.message} ({s.source_agent})" for s in ranked[:8]],
            "signals_by_agent": {a: sum(1 for s in all_signals if s.source_agent == a) for a in {s.source_agent for s in all_signals}},
            "name_match_scores": name_scores,
            "cross_application_matches": {k: [h["app_id"] for h in v_] for k, v_ in matches.items()},
            "bureau": bureau,
        }
        if band in ("high", "critical"):
            res.open_items.append({"assignee": "credit_officer", "category": "fraud_review", "blocking": True,
                                   "question": f"Fraud risk {band} ({score}). Review: " + "; ".join(r for r in res.output["top_reasons"][:3])})
        res.summary = f"Fraud risk {score} ({band}) from {len(all_signals)} fraud-relevant signal(s); {len(own)} raised by fraud checks"
        return res
