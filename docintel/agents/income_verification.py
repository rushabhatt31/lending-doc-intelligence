"""Income Verification Agent (Retail).

Triangulates salary across payslips, Form 16, bank credits and EPFO;
checks employer consistency and salary regularity; then computes FOIR and
the maximum affordable loan.
"""
from __future__ import annotations

import statistics

from ..models import AgentResult, Severity
from ..utils import emi, name_similarity, normalize_name, principal_for_emi
from .base import AgentContext, BaseAgent


def _pct_gap(a: float, b: float) -> float:
    return abs(a - b) * 100 / max(a, b) if a and b else 0.0


class IncomeVerificationAgent(BaseAgent):
    name = "income_verification"
    segments = ("retail",)

    def _run(self, ctx: AgentContext) -> AgentResult:
        res = AgentResult(agent=self.name)
        cfg, applicant = ctx.config, ctx.app.applicant
        sources: dict[str, float] = {}
        employers: dict[str, str] = {}

        # 1. Payslips
        slips = [d for d in ctx.docs("salary_slip") if d.fields.get("net_salary")]
        if slips:
            sources["payslip_net"] = statistics.median(d.fields["net_salary"] for d in slips)
            gross = [d.fields["gross_salary"] for d in slips if d.fields.get("gross_salary")]
            if gross:
                sources["payslip_gross"] = statistics.median(gross)
            if slips[0].fields.get("employer"):
                employers["payslip"] = slips[0].fields["employer"]

        # 2. Form 16
        f16 = next((d for d in ctx.docs("form_16") if d.fields.get("gross_salary")), None)
        if f16:
            sources["form16_monthly_gross"] = round(f16.fields["gross_salary"] / 12, 2)
            if f16.fields.get("employer"):
                employers["form16"] = f16.fields["employer"]

        # 3. Bank credits (what actually landed)
        bank = ctx.result("bank_statement_analyser")
        salary_credits, inferred = [], False
        if bank and bank.status == "ok":
            salary_credits = bank.output.get("salary_credits", [])
            if not salary_credits and bank.output.get("recurring_credits"):
                top = bank.output["recurring_credits"][0]
                sources["bank_recurring_credit"] = top["typical_amount"]
                inferred = True
            if salary_credits:
                recent = [c["amount"] for c in salary_credits[-3:]]
                sources["bank_salary"] = statistics.median(recent)
                employers["bank_narration"] = salary_credits[-1]["narration"]

        # 4. EPFO (document or API)
        epfo_doc = next(iter(ctx.docs("epfo_passbook")), None)
        uan = epfo_doc.fields.get("uan") if epfo_doc else None
        epfo = ctx.connectors.employment.lookup(uan, applicant.pan)
        ctx.audit.record(ctx.app.id, self.name, "employment_lookup", {"uan_present": bool(uan), "status": epfo.get("status")})
        if epfo_doc and epfo_doc.fields.get("employer"):
            employers["epfo"] = epfo_doc.fields["employer"]
        elif epfo.get("employer"):
            employers["epfo"] = epfo["employer"]
        else:
            res.open_items.append({"assignee": "customer", "category": "verification", "blocking": False,
                                   "required_doc_type": "epfo_passbook",
                                   "question": "Share your UAN or EPF passbook to verify current employment."})

        if not sources:
            res.status, res.summary = "skipped", "No income evidence"
            res.open_items.append({"assignee": "customer", "category": "missing_document", "blocking": True,
                                   "required_doc_type": "salary_slip|form_16",
                                   "question": "Upload last 3 months' salary slips or latest Form 16."})
            return res

        # --- verified income: never higher than cash actually received ---
        net_candidates = [sources[k] for k in ("payslip_net", "bank_salary") if k in sources]
        if net_candidates:
            verified = min(net_candidates)
            basis = "min(payslip net, bank salary credit)" if len(net_candidates) == 2 else (
                "bank salary credit" if "bank_salary" in sources else "payslip net (unconfirmed by bank)")
        elif "bank_recurring_credit" in sources:
            verified, basis = sources["bank_recurring_credit"], "recurring bank credit (inferred salary)"
        else:
            verified, basis = sources["form16_monthly_gross"] * 0.8, "Form 16 gross × 0.8 (estimated net)"

        # --- consistency checks ---
        if "payslip_net" in sources and "bank_salary" in sources:
            gap = _pct_gap(sources["payslip_net"], sources["bank_salary"])
            if gap > 10:
                res.signals.append(self.signal("SALARY_SLIP_BANK_MISMATCH",
                                               f"Payslip net vs bank credit differ by {gap:.0f}%", Severity.MEDIUM))
                res.open_items.append({"assignee": "credit_officer", "category": "discrepancy", "blocking": False,
                                       "question": f"Payslip net ₹{sources['payslip_net']:,.0f} vs bank salary "
                                                   f"₹{sources['bank_salary']:,.0f} ({gap:.0f}% gap). Accept lower figure or seek explanation?"})
        if "payslip_gross" in sources and "form16_monthly_gross" in sources:
            gap = _pct_gap(sources["payslip_gross"], sources["form16_monthly_gross"])
            if gap > 20:
                res.signals.append(self.signal("SALARY_FORM16_MISMATCH", f"Payslip gross vs Form 16 differ by {gap:.0f}%",
                                               Severity.MEDIUM))
        if inferred:
            res.signals.append(self.signal("SALARY_NOT_TAGGED", "No credit tagged as salary; income inferred from recurring credits",
                                           Severity.LOW))
        if applicant.declared_monthly_income and applicant.declared_monthly_income > verified * 1.2:
            res.signals.append(self.signal("INCOME_OVERSTATED",
                                           f"Declared ₹{applicant.declared_monthly_income:,.0f} vs verified ₹{verified:,.0f}",
                                           Severity.MEDIUM))

        # Employer consistency
        employer_match = {}
        ref = employers.get("payslip") or employers.get("form16")
        if ref:
            for src, emp in employers.items():
                if src == "bank_narration":
                    tokens = set(normalize_name(ref).split())
                    score = len(tokens & set(normalize_name(emp).split())) / len(tokens) if tokens else 0
                else:
                    score = name_similarity(ref, emp)
                employer_match[src] = round(score, 2)
            weak = [s for s, sc in employer_match.items() if sc < 0.6]
            if weak:
                res.signals.append(self.signal("EMPLOYER_MISMATCH", f"Employer name differs across: {', '.join(weak)}",
                                               Severity.MEDIUM, employers=employers))

        # Regularity
        months_paid = sorted({c["date"][:7] for c in salary_credits})
        if bank and bank.status == "ok" and salary_credits:
            expected = min(bank.output["period"]["months"], 6)
            if len(months_paid) < expected - 1:
                res.signals.append(self.signal("IRREGULAR_SALARY",
                                               f"Salary seen in {len(months_paid)} of {expected} months", Severity.MEDIUM))

        # --- affordability ---
        bank_emi = bank.output.get("total_monthly_emi_obligation", 0) if bank and bank.status == "ok" else 0
        existing = max(applicant.existing_emi or 0, bank_emi)
        rate, tenure = cfg.assumed_interest_rate_pct, applicant.requested_tenure_months or 36
        proposed = emi(applicant.requested_amount, rate, tenure) if applicant.requested_amount else 0
        foir = round((existing + proposed) * 100 / verified, 1) if verified else None
        max_emi = max(0.0, verified * cfg.foir_cap_pct / 100 - existing)
        max_loan = principal_for_emi(max_emi, rate, tenure)
        if foir is not None and foir > cfg.foir_cap_pct:
            res.signals.append(self.signal("FOIR_EXCEEDS_CAP", f"FOIR {foir}% > cap {cfg.foir_cap_pct}%", Severity.HIGH))
            res.open_items.append({"assignee": "credit_officer", "category": "structuring", "blocking": True,
                                   "question": f"FOIR {foir}% exceeds {cfg.foir_cap_pct}%. Max eligible ≈ ₹{max_loan:,.0f} "
                                               f"over {tenure} months. Reduce amount, extend tenure, or add co-applicant?"})

        res.output = {
            "income_sources": {k: round(v_, 2) for k, v_ in sources.items()},
            "verified_monthly_income": round(verified, 2), "verification_basis": basis,
            "employers": employers, "employer_match": employer_match,
            "epfo": {k: v_ for k, v_ in epfo.items() if k != "raw"},
            "salary_months_observed": months_paid,
            "existing_emi": round(existing, 2), "proposed_emi": round(proposed, 2),
            "assumed_rate_pct": rate, "tenure_months": tenure,
            "foir_pct": foir, "foir_cap_pct": cfg.foir_cap_pct,
            "max_eligible_emi": round(max_emi, 2), "max_eligible_loan": round(max_loan, -3),
        }
        res.summary = (f"Verified income ₹{verified:,.0f}/month ({basis}); FOIR {foir}%; "
                       f"max eligible ≈ ₹{max_loan:,.0f}")
        return res
