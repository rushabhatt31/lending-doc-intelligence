"""Financial Analysis Agent (SME).

Takes multi-year P&L / Balance Sheet / Cash Flow (structured JSON, or
extracted from PDF via LLM), computes ~30 credit ratios, multi-year trends,
and benchmarks each against sector bands.
"""
from __future__ import annotations

from ..models import AgentResult, Severity
from .base import AgentContext, BaseAgent

LINE_ITEMS = [
    "revenue", "cogs", "employee_cost", "other_expenses", "depreciation", "interest", "other_income", "tax",
    "total_assets", "current_assets", "current_liabilities", "inventory", "receivables", "payables", "cash",
    "net_worth", "long_term_debt", "short_term_debt", "current_portion_ltd", "fixed_assets",
    "cfo", "capex", "intangibles",
]

# sector -> ratio -> (healthy threshold, direction). direction "min" = higher is better.
BENCHMARKS: dict[str, dict[str, tuple[float, str]]] = {
    "default": {
        "current_ratio": (1.2, "min"), "debt_equity": (2.0, "max"), "tol_tnw": (3.0, "max"),
        "interest_coverage": (2.0, "min"), "dscr": (1.25, "min"), "ebitda_margin_pct": (8.0, "min"),
        "debt_ebitda": (4.0, "max"), "receivable_days": (90, "max"), "roce_pct": (12.0, "min"),
    },
    "manufacturing": {"ebitda_margin_pct": (10.0, "min"), "inventory_days": (90, "max"), "receivable_days": (75, "max")},
    "trading": {"ebitda_margin_pct": (3.0, "min"), "current_ratio": (1.1, "min"), "inventory_days": (45, "max"), "tol_tnw": (4.0, "max")},
    "services": {"ebitda_margin_pct": (12.0, "min"), "receivable_days": (60, "max"), "debt_equity": (1.5, "max")},
    "construction": {"receivable_days": (120, "max"), "tol_tnw": (3.5, "max")},
}
CORE_RATIOS = {"dscr", "interest_coverage", "debt_equity", "current_ratio"}


def _div(a, b):
    try:
        return a / b if a is not None and b not in (None, 0) else None
    except TypeError:
        return None


def _g(d: dict, k: str) -> float:
    return float(d.get(k) or 0.0)


def compute_ratios(y: dict[str, float]) -> dict[str, float | None]:
    rev = _g(y, "revenue")
    ebitda = rev - _g(y, "cogs") - _g(y, "employee_cost") - _g(y, "other_expenses")
    ebit = ebitda - _g(y, "depreciation")
    pbt = ebit - _g(y, "interest") + _g(y, "other_income")
    pat = pbt - _g(y, "tax")
    debt = _g(y, "long_term_debt") + _g(y, "short_term_debt")
    nw = _g(y, "net_worth")
    tnw = nw - _g(y, "intangibles")
    tol = _g(y, "total_assets") - nw
    cap_employed = nw + debt
    wc = _g(y, "current_assets") - _g(y, "current_liabilities")
    cogs = _g(y, "cogs") or rev
    r = {
        "revenue": rev, "ebitda": ebitda, "ebit": ebit, "pbt": pbt, "pat": pat, "total_debt": debt,
        "gross_margin_pct": _div((rev - _g(y, "cogs")) * 100, rev),
        "ebitda_margin_pct": _div(ebitda * 100, rev),
        "ebit_margin_pct": _div(ebit * 100, rev),
        "pat_margin_pct": _div(pat * 100, rev),
        "roe_pct": _div(pat * 100, nw),
        "roa_pct": _div(pat * 100, _g(y, "total_assets")),
        "roce_pct": _div(ebit * 100, cap_employed),
        "current_ratio": _div(_g(y, "current_assets"), _g(y, "current_liabilities")),
        "quick_ratio": _div(_g(y, "current_assets") - _g(y, "inventory"), _g(y, "current_liabilities")),
        "cash_ratio": _div(_g(y, "cash"), _g(y, "current_liabilities")),
        "working_capital": wc,
        "debt_equity": _div(debt, nw),
        "tol_tnw": _div(tol, tnw),
        "debt_ebitda": _div(debt, ebitda) if ebitda > 0 else None,
        "net_debt_ebitda": _div(debt - _g(y, "cash"), ebitda) if ebitda > 0 else None,
        "interest_coverage": _div(ebit, _g(y, "interest")),
        "dscr": _div(pat + _g(y, "depreciation") + _g(y, "interest"), _g(y, "interest") + _g(y, "current_portion_ltd")),
        "asset_turnover": _div(rev, _g(y, "total_assets")),
        "fixed_asset_turnover": _div(rev, _g(y, "fixed_assets")),
        "inventory_days": _div(_g(y, "inventory") * 365, cogs),
        "receivable_days": _div(_g(y, "receivables") * 365, rev),
        "payable_days": _div(_g(y, "payables") * 365, cogs),
        "cfo_to_pat": _div(_g(y, "cfo"), pat) if pat else None,
        "free_cash_flow": _g(y, "cfo") - _g(y, "capex") if y.get("cfo") is not None else None,
        "cfo_to_debt": _div(_g(y, "cfo"), debt),
        "equity_ratio": _div(nw, _g(y, "total_assets")),
        "wc_to_revenue_pct": _div(wc * 100, rev),
    }
    if None not in (r["inventory_days"], r["receivable_days"], r["payable_days"]):
        r["cash_conversion_cycle"] = r["inventory_days"] + r["receivable_days"] - r["payable_days"]
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}


def growth(series: list[float | None]) -> float | None:
    if len(series) < 2 or not series[-2]:
        return None
    return round((series[-1] - series[-2]) * 100 / abs(series[-2]), 2)


def cagr(series: list[float | None]) -> float | None:
    if len(series) < 3 or not series[0] or not series[-1] or series[0] <= 0 or series[-1] <= 0:
        return None
    return round(((series[-1] / series[0]) ** (1 / (len(series) - 1)) - 1) * 100, 2)


class FinancialAnalysisAgent(BaseAgent):
    name = "financial_analysis"
    segments = ("sme",)

    def _load(self, ctx: AgentContext) -> dict[str, dict] | None:
        for doc in ctx.docs("financial_statements"):
            d = doc.data or {}
            years = d.get("years") or d.get("financials")
            if years:
                return dict(sorted(years.items()))
        pdfs = ctx.docs("balance_sheet", "profit_and_loss")
        if pdfs and ctx.llm.available:
            text = "\n\n".join(p.text for p in pdfs)
            ans = ctx.llm.extract_json(
                "Extract multi-year financials from these statements. Return "
                '{"years": {"FY2024": {<line items>}, ...}} using these keys (absolute numbers, INR): '
                + ", ".join(LINE_ITEMS), text)
            if ans and ans.get("years"):
                ctx.audit.record(ctx.app.id, self.name, "llm_extraction", {"years": list(ans["years"])})
                return dict(sorted(ans["years"].items()))
        return None

    def _run(self, ctx: AgentContext) -> AgentResult:
        res = AgentResult(agent=self.name)
        years = self._load(ctx)
        if not years:
            res.status, res.summary = "skipped", "No financial statements available"
            res.open_items.append({
                "assignee": "customer", "category": "missing_document", "blocking": True,
                "required_doc_type": "financial_statements|balance_sheet|profit_and_loss",
                "question": "Please upload audited financials (P&L, Balance Sheet, Cash Flow) for the last 2-3 years.",
            })
            return res

        ratios = {fy: compute_ratios(v) for fy, v in years.items()}
        fys = list(ratios)
        latest = ratios[fys[-1]]
        trends = {}
        for k in ["revenue", "ebitda", "pat", "total_debt"]:
            series = [ratios[fy][k] for fy in fys]
            trends[k] = {"series": dict(zip(fys, series)), "yoy_pct": growth(series), "cagr_pct": cagr(series)}
        for k in ["ebitda_margin_pct", "dscr", "debt_equity", "current_ratio", "receivable_days"]:
            trends[k] = {"series": {fy: ratios[fy].get(k) for fy in fys}}

        sector = (ctx.app.applicant.sector or "default").lower()
        bands = {**BENCHMARKS["default"], **BENCHMARKS.get(sector, {})}
        benchmark = {}
        for ratio, (threshold, direction) in bands.items():
            val = latest.get(ratio)
            if val is None:
                benchmark[ratio] = {"value": None, "threshold": threshold, "status": "n/a"}
                continue
            ok = val >= threshold if direction == "min" else val <= threshold
            benchmark[ratio] = {"value": val, "threshold": threshold, "direction": direction,
                                "status": "within" if ok else "breach"}
            if not ok:
                sev = Severity.HIGH if ratio in CORE_RATIOS else Severity.MEDIUM
                res.signals.append(self.signal(f"RATIO_BREACH_{ratio.upper()}",
                                               f"{ratio} = {val} vs sector {'min' if direction == 'min' else 'max'} {threshold}", sev))

        if latest["pat"] < 0:
            res.signals.append(self.signal("LOSS_MAKING", f"Net loss in {fys[-1]}", Severity.HIGH))
        rev_yoy = trends["revenue"]["yoy_pct"]
        if rev_yoy is not None and rev_yoy < -15:
            res.signals.append(self.signal("REVENUE_DECLINE", f"Revenue down {rev_yoy}% YoY", Severity.MEDIUM))
        if (latest.get("cfo_to_pat") or 1) < 0.5 and latest["pat"] > 0:
            res.signals.append(self.signal("WEAK_CASH_CONVERSION", "Operating cash flow < 50% of PAT (earnings quality)", Severity.MEDIUM))
        for fy, v in years.items():
            if v.get("total_liabilities") is not None and _g(v, "total_assets"):
                gap = abs(_g(v, "total_assets") - _g(v, "net_worth") - _g(v, "total_liabilities"))
                if gap > 0.01 * _g(v, "total_assets"):
                    res.signals.append(self.signal("BALANCE_SHEET_DOES_NOT_TALLY",
                                                   f"{fy}: assets ≠ net worth + liabilities (gap ₹{gap:,.0f})", Severity.HIGH))

        res.output = {
            "years": fys,
            "ratios": ratios,
            "ratio_count": sum(1 for v in latest.values() if v is not None),
            "trends": trends,
            "sector": sector,
            "benchmark": benchmark,
            "latest_revenue": latest["revenue"],
        }
        breaches = sum(1 for b in benchmark.values() if b["status"] == "breach")
        res.summary = (f"{len(fys)} years analysed ({fys[0]}–{fys[-1]}); DSCR {latest.get('dscr')}, "
                       f"D/E {latest.get('debt_equity')}; revenue YoY {rev_yoy}%; {breaches} sector benchmark breach(es)")
        return res


__all__ = ["FinancialAnalysisAgent", "compute_ratios", "LINE_ITEMS", "BENCHMARKS"]
