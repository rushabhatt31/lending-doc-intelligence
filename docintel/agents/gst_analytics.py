"""GST Analytics Agent (SME).

GSTR-1 (what the business declares it sold) vs GSTR-3B (what it paid tax on)
reconciliation, filing discipline, compliance score, buyer/anchor
concentration, sector risk, and a cross-check of GST turnover against bank credits.
"""
from __future__ import annotations

import statistics
from datetime import date

from ..models import AgentResult, Severity
from ..utils import parse_date
from .. import validators as v
from .base import AgentContext, BaseAgent

DUE_DAY = {"gstr1": 11, "gstr3b": 20}

SECTOR_RISK = {
    "real_estate": "high", "construction": "high", "hospitality": "high", "gems_jewellery": "high", "aviation": "high",
    "textiles": "medium", "trading": "medium", "auto_components": "medium", "logistics": "medium", "manufacturing": "medium",
    "pharma": "low", "fmcg": "low", "it_services": "low", "healthcare": "low", "services": "low", "food_processing": "low",
}

STATE_CODES = {"07": "Delhi", "09": "Uttar Pradesh", "19": "West Bengal", "24": "Gujarat", "27": "Maharashtra",
               "29": "Karnataka", "32": "Kerala", "33": "Tamil Nadu", "36": "Telangana", "06": "Haryana"}


def _due(period: str, kind: str) -> date:
    y, m = map(int, period.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return date(y, m, DUE_DAY[kind])


def _months_between(a: str, b: str) -> list[str]:
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    out = []
    while (ya, ma) <= (yb, mb):
        out.append(f"{ya:04d}-{ma:02d}")
        ya, ma = (ya + 1, 1) if ma == 12 else (ya, ma + 1)
    return out


class GSTAnalyticsAgent(BaseAgent):
    name = "gst_analytics"
    segments = ("sme",)

    def _run(self, ctx: AgentContext) -> AgentResult:
        res = AgentResult(agent=self.name)
        docs = ctx.docs("gst_returns_data")
        if not docs:
            res.status, res.summary = "skipped", "No GST return data"
            res.open_items.append({
                "assignee": "customer", "category": "consent", "blocking": True, "required_doc_type": "gst_returns_data",
                "question": "Share GST returns for the last 12 months (GSP consent / OTP-based GST API access).",
            })
            return res

        data = docs[0].data
        gstin = data.get("gstin", "")
        g1 = {r["period"]: r for r in data.get("gstr1", [])}
        g3 = {r["period"]: r for r in data.get("gstr3b", [])}
        all_p = sorted(set(g1) | set(g3))
        expected = _months_between(all_p[0], all_p[-1]) if all_p else []
        score, deductions = 100.0, []

        def deduct(points: float, reason: str):
            nonlocal score
            score -= points
            deductions.append({"points": points, "reason": reason})

        # --- identity ---
        if not v.gstin_valid(gstin):
            res.signals.append(self.signal("GSTIN_INVALID", f"GSTIN {gstin} fails checksum", Severity.HIGH, docs[0]))
        pan = ctx.app.applicant.pan
        if pan and v.gstin_pan(gstin) and v.gstin_pan(gstin) != pan:
            res.signals.append(self.signal("GSTIN_PAN_MISMATCH",
                                           f"GSTIN is registered to PAN {v.gstin_pan(gstin)}, applicant PAN is {pan}",
                                           Severity.MEDIUM, docs[0]))

        # --- reconciliation ---
        tol = ctx.config.recon_variance_tolerance_pct
        recon, flagged = [], []
        for p in expected:
            a = float(g1.get(p, {}).get("taxable_value") or 0)
            b = float(g3.get(p, {}).get("taxable_value") or 0)
            var = round((a - b) * 100 / a, 2) if a else (0.0 if not b else -100.0)
            row = {"period": p, "gstr1_taxable": a, "gstr3b_taxable": b, "variance_pct": var}
            recon.append(row)
            if abs(var) > tol:
                flagged.append(row)
        if flagged:
            deduct(min(25, 4 * len(flagged)), f"{len(flagged)} month(s) with GSTR-1 vs 3B variance > {tol}%")
            sev = Severity.HIGH if len(flagged) >= 3 else Severity.MEDIUM
            res.signals.append(self.signal("GST_RECON_VARIANCE",
                                           f"GSTR-1 vs 3B variance > {tol}% in {len(flagged)} month(s)", sev,
                                           periods=[f["period"] for f in flagged]))
        tot1 = sum(r["gstr1_taxable"] for r in recon)
        tot3 = sum(r["gstr3b_taxable"] for r in recon)
        annual_var = round((tot1 - tot3) * 100 / tot1, 2) if tot1 else None

        # --- filing discipline ---
        late, missing = [], []
        for p in expected:
            for kind, src in (("gstr1", g1), ("gstr3b", g3)):
                rec = src.get(p)
                if not rec:
                    missing.append(f"{kind}:{p}")
                    continue
                filed = parse_date(str(rec.get("filed_on") or ""))
                if filed and filed > _due(p, kind):
                    late.append({"return": kind, "period": p, "days_late": (filed - _due(p, kind)).days})
        if late:
            deduct(min(30, 2 * len(late)), f"{len(late)} late filing(s)")
        if missing:
            deduct(min(30, 5 * len(missing)), f"{len(missing)} missing return(s)")
            res.signals.append(self.signal("GST_RETURNS_MISSING", f"{len(missing)} return(s) not filed", Severity.HIGH, missing=missing))
        nil_months = [p for p in expected if float(g1.get(p, {}).get("taxable_value") or 0) == 0]
        if nil_months:
            deduct(3 * len(nil_months), f"{len(nil_months)} nil month(s)")

        # --- ITC behaviour ---
        itc_ratio = None
        tax_out = sum(float(r.get("tax_liability") or r.get("tax") or 0) for r in g3.values())
        itc = sum(float(r.get("itc_claimed") or 0) for r in g3.values())
        if tax_out:
            itc_ratio = round(itc / tax_out, 3)
            if itc_ratio > 0.95:
                deduct(10, "ITC offsets >95% of output tax")
                res.signals.append(self.signal("HIGH_ITC_UTILISATION", f"ITC covers {itc_ratio:.0%} of output tax "
                                               "(check for circular trading)", Severity.MEDIUM))

        # --- concentration & sector ---
        buyers = sorted(data.get("top_buyers", []), key=lambda b: -float(b.get("share_pct", 0)))
        top_share = float(buyers[0]["share_pct"]) if buyers else None
        if top_share and top_share > 40:
            sev = Severity.HIGH if top_share > 60 else Severity.MEDIUM
            res.signals.append(self.signal("ANCHOR_CONCENTRATION", f"Top buyer = {top_share:.0f}% of sales", sev,
                                           anchor=buyers[0].get("name")))
        sector = (data.get("sector") or ctx.app.applicant.sector or "").lower()
        sector_risk = SECTOR_RISK.get(sector, "unknown")
        anchor_map = [{**b, "sector_risk": SECTOR_RISK.get((b.get("sector") or "").lower(), "unknown")} for b in buyers]
        if sector_risk == "high":
            res.signals.append(self.signal("HIGH_RISK_SECTOR", f"Borrower sector '{sector}' is high-risk", Severity.MEDIUM))

        # --- GST turnover vs bank credits ---
        monthly_turnover = [float(g3.get(p, {}).get("taxable_value") or 0) for p in expected]
        avg_turnover = statistics.mean(monthly_turnover) if monthly_turnover else 0
        bank = ctx.result("bank_statement_analyser")
        bank_ratio = None
        if bank and bank.status == "ok" and avg_turnover:
            bank_ratio = round(bank.output["avg_monthly_credits"] / (avg_turnover * 1.18), 2)
            if bank_ratio < 0.6:
                res.signals.append(self.signal("BANK_CREDITS_BELOW_GST", f"Bank credits only {bank_ratio:.0%} of GST sales "
                                               "(other accounts not disclosed?)", Severity.MEDIUM))
                res.open_items.append({"assignee": "customer", "category": "discrepancy", "blocking": False,
                                       "required_doc_type": "bank_statement",
                                       "question": "Sales in GST returns exceed credits in the shared bank account. "
                                                   "Please share statements of all operative business accounts."})
            elif bank_ratio > 1.6:
                res.signals.append(self.signal("BANK_CREDITS_ABOVE_GST", f"Bank credits {bank_ratio:.1f}x GST sales "
                                               "(unreported turnover or rotation)", Severity.MEDIUM))

        score = max(0.0, round(score, 1))
        band = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 50 else "D"
        if band == "D":
            res.signals.append(self.signal("POOR_GST_COMPLIANCE", f"GST compliance score {score}", Severity.HIGH))

        res.output = {
            "gstin": gstin, "state": STATE_CODES.get(gstin[:2], gstin[:2]),
            "periods": len(expected),
            "annual_gstr1_taxable": tot1, "annual_gstr3b_taxable": tot3, "annual_variance_pct": annual_var,
            "avg_monthly_turnover": round(avg_turnover, 2),
            "reconciliation": recon,
            "late_filings": late, "missing_returns": missing, "nil_months": nil_months,
            "itc_to_output_ratio": itc_ratio,
            "compliance_score": score, "compliance_band": band, "score_deductions": deductions,
            "sector": sector, "sector_risk": sector_risk,
            "top_buyer_share_pct": top_share, "anchor_risk_map": anchor_map,
            "bank_to_gst_ratio": bank_ratio,
        }
        res.summary = (f"{len(expected)} months; annual GSTR-1 vs 3B variance {annual_var}%; "
                       f"compliance {score} ({band}); {len(late)} late, {len(missing)} missing; sector risk {sector_risk}")
        return res
