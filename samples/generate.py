"""Generate synthetic sample documents (all people, firms and numbers are fictitious).

    python samples/generate.py

Produces three cases:
  retail_clean/  salaried borrower, consistent documents        -> should be STP / proceed
  retail_risky/  edited statement, DOB + name mismatch, reused doc -> fraud review
  sme/           MSME manufacturer with GST + 3-yr financials     -> refer / conditions
"""
from __future__ import annotations

import csv
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docintel.validators import gstin_check_char, verhoeff_check_digit  # noqa: E402

OUT = Path(__file__).parent
random.seed(7)

AADHAAR = "23456789012" + verhoeff_check_digit("23456789012")
BIZ_PAN = "AAHFG1234Q"
GSTIN = "29" + BIZ_PAN + "1Z"
GSTIN += gstin_check_char(GSTIN)


def w(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n")


def month_iter(start: date, n: int):
    y, m = start.year, start.month
    for _ in range(n):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def bank_rows(start: date, months: int, opening: float, plan) -> list[dict]:
    rows, bal = [], opening
    for y, m in month_iter(start, months):
        for day, narr, dr, cr in sorted(plan(y, m), key=lambda t: t[0]):
            bal = round(bal + cr - dr, 2)
            rows.append({"Date": date(y, m, day).strftime("%d/%m/%Y"), "Narration": narr,
                         "Debit": f"{dr:.2f}" if dr else "", "Credit": f"{cr:.2f}" if cr else "", "Balance": f"{bal:.2f}"})
    return rows


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)


MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def retail_plan(y, m):
    return [
        (1, f"NEFT CR-ACME TECHNOLOGIES PVT LTD-SALARY {MONTHS[m-1]}", 0, 92400),
        (5, "NACH DR-ZENITH FINANCE LTD-EMI 4471", 12500, 0),
        (7, "UPI/rentpay/LANDLORD RENT", 25000, 0),
        (9, "BESCOM ELECTRICITY BILL", round(random.uniform(1400, 2600), 2), 0),
        (12, "UPI/swiggy/food", round(random.uniform(800, 2500), 2), 0),
        (15, "ATM CASH WDL MG ROAD", 5000, 0),
        (18, "UPI/amazon/shopping", round(random.uniform(1500, 6000), 2), 0),
        (22, "SIP MUTUAL FUND", 10000, 0),
        (26, "UPI/bigbasket/groceries", round(random.uniform(3000, 6000), 2), 0),
        (28, "AIRTEL POSTPAID", 999, 0),
    ]


def retail_clean():
    d = OUT / "retail_clean"
    w(d / "pan_card.txt", """
INCOME TAX DEPARTMENT        GOVT. OF INDIA
Permanent Account Number Card
PAN: ABCPS1234K
Name: RAHUL SHARMA
Father's Name: VIJAY SHARMA
Date of Birth: 14/03/1991
Signature
""")
    w(d / "aadhaar.txt", f"""
Unique Identification Authority of India
Government of India
Name: Rahul Sharma
DOB: 14/03/1991
MALE
{AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}
Aadhaar - Mera Aadhaar, Meri Pehchaan
VID: 9134 5678 1234 5678
""")
    for mon, pm in (("Aug", "August 2026"), ("Sep", "September 2026")):
        w(d / f"payslip_{mon.lower()}_2026.txt", f"""
PAYSLIP
Company: Acme Technologies Pvt Ltd
Pay Period: {pm}
Employee Name: Rahul Sharma
Employee ID: ACM-20417        PAN: ABCPS1234K
Earnings                         Deductions
Basic          57,500            Provident Fund   6,900
HRA            28,750            Professional Tax   200
Special Allow  28,750            Income Tax      15,500
Gross Earnings: 1,15,000.00
Total Deductions: 22,600.00
Net Pay: 92,400.00
""")
    w(d / "form16_fy2025-26.txt", """
FORM NO. 16
Certificate under section 203 of the Income-tax Act, 1961 for tax deducted at source on salary
Name and address of the Employer: Acme Technologies Private Limited, Bengaluru
TAN of the Deductor: BLRA12345C
PAN of the Employee: ABCPS1234K
Assessment Year: 2026-27
Gross Salary: 13,20,000.00
Total Tax Deducted: 1,74,000.00
""")
    w(d / "epf_passbook.txt", """
Employees' Provident Fund Organisation
Member Passbook
UAN: 100234567890
Member Name: RAHUL SHARMA
Establishment ID: KNBNG0012345000
Establishment Name: ACME TECHNOLOGIES PRIVATE LIMITED
Wage Month   Employee Share   Employer Share
Aug-2026     6,900            2,108
Sep-2026     6,900            2,108
""")
    write_csv(d / "bank_statement_apr-sep_2026.csv", bank_rows(date(2026, 4, 1), 6, 48250.0, retail_plan))


def retail_risky():
    d = OUT / "retail_risky"
    w(d / "pan_card.txt", """
INCOME TAX DEPARTMENT        GOVT. OF INDIA
Permanent Account Number Card
PAN: BQRPK4821M
Name: KARAN MEHTA
Date of Birth: 02/11/1994
Signature
""")
    w(d / "aadhaar.txt", f"""
Unique Identification Authority of India
Government of India
Name: Karan Malhotra
DOB: 02/11/1992
MALE
{AADHAAR[:4]} {AADHAAR[4:8]} {int(AADHAAR[8:]) ^ 1:04d}
Aadhaar - Mera Aadhaar, Meri Pehchaan
""")
    # Payslip reused byte-for-byte from the clean applicant -> duplicate document
    src = (OUT / "retail_clean" / "payslip_sep_2026.txt").read_text()
    (d / "payslip_sep_2026.txt").write_text(src)

    # Text statement where two balances were edited upward
    lines = ["STATEMENT OF ACCOUNT", "Bank: Example Bank Ltd", "Account Holder: KARAN MEHTA",
             "Account No: XXXXXXXX7731", "IFSC: EXMP0001234",
             "Opening Balance: 12,400.00", "", "Date        Narration                               Amount        Balance"]
    bal = 12400.0
    tx = []
    for y, m in month_iter(date(2026, 7, 1), 3):
        tx += [
            (date(y, m, 1), f"IMPS CR-QUICKPAY SOLUTIONS-SAL {MONTHS[m-1]}", 0, 64000),
            (date(y, m, 6), "NACH DR-RAPIDLOAN FINSERV EMI", 18200, 0),
            (date(y, m, 10), "UPI/p2p/transfer", 15000, 0),
            (date(y, m, 20), "ATM CASH WDL", 10000, 0),
        ]
    tx.append((date(2026, 9, 7), "NACH RTN-RAPIDLOAN FINSERV-INSUFF FUNDS", 0, 18200))
    tx.append((date(2026, 9, 7), "NACH RTN CHG", 590, 0))
    tx.append((date(2026, 9, 28), "CASH DEPOSIT CDM", 0, 50000))
    for i, (dt, narr, dr, cr) in enumerate(sorted(tx, key=lambda t: t[0])):
        bal = round(bal + cr - dr, 2)
        shown = bal + (40000 if i in (5, 6) else 0)  # tampering
        amt = dr or cr
        lines.append(f"{dt.strftime('%d/%m/%Y')}  {narr:<40} {amt:>12,.2f} {shown:>14,.2f}")
    lines.append(f"Closing Balance: {bal:,.2f}")
    w(d / "bank_statement_jul-sep_2026.txt", "\n".join(lines))


def sme():
    d = OUT / "sme"
    w(d / "pan_card.txt", f"""
INCOME TAX DEPARTMENT        GOVT. OF INDIA
Permanent Account Number Card
PAN: {BIZ_PAN}
Name: GANESH AUTO COMPONENTS
Date of Incorporation/Formation: 18/06/2014
Signature
""")
    w(d / "gst_certificate.txt", f"""
Government of India
Form GST REG-06
Registration Certificate
Goods and Services Tax
Registration Number: {GSTIN}
Legal Name: GANESH AUTO COMPONENTS
Trade Name: GANESH AUTO COMPONENTS
Constitution of Business: Partnership
Address of Principal Place of Business: Plot 42, Peenya Industrial Area, Bengaluru, Karnataka
""")
    gstr1, gstr3b = [], []
    for i, (y, m) in enumerate(month_iter(date(2025, 10, 1), 12)):
        p = f"{y:04d}-{m:02d}"
        tv = round(3_800_000 * (1 + 0.012 * i) * random.uniform(0.92, 1.08), -3)
        tv3 = tv * (0.84 if p in ("2026-02", "2026-06") else random.uniform(0.985, 1.0))
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        late = p in ("2026-03",)
        gstr1.append({"period": p, "taxable_value": tv, "tax": round(tv * 0.18, 2),
                      "filed_on": str(date(ny, nm, 18 if late else 10))})
        gstr3b.append({"period": p, "taxable_value": round(tv3, 2), "tax_liability": round(tv3 * 0.18, 2),
                       "itc_claimed": round(tv3 * 0.18 * 0.78, 2), "filed_on": str(date(ny, nm, 19))})
    (d / "gst_returns.json").write_text(json.dumps({
        "gstin": GSTIN, "legal_name": "GANESH AUTO COMPONENTS", "sector": "auto_components",
        "gstr1": gstr1, "gstr3b": gstr3b,
        "top_buyers": [
            {"name": "Anchor OEM Motors Ltd", "gstin": "29AABCA1111A1Z5", "share_pct": 48, "sector": "auto_components"},
            {"name": "Delta Tractors Pvt Ltd", "gstin": "33AABCD2222B1Z1", "share_pct": 21, "sector": "manufacturing"},
            {"name": "Metro Spares Trading", "gstin": "29AAFFM3333C1Z2", "share_pct": 9, "sector": "trading"},
        ],
    }, indent=2))
    fin = {"years": {
        "FY2024": dict(revenue=38_500_000, cogs=26_200_000, employee_cost=4_100_000, other_expenses=3_300_000,
                       depreciation=900_000, interest=1_150_000, other_income=120_000, tax=780_000,
                       total_assets=31_000_000, total_liabilities=19_400_000, current_assets=17_800_000,
                       current_liabilities=12_100_000, inventory=6_900_000, receivables=7_600_000,
                       payables=5_400_000, cash=1_300_000, net_worth=11_600_000, long_term_debt=6_200_000,
                       short_term_debt=4_800_000, current_portion_ltd=1_400_000, fixed_assets=12_500_000,
                       cfo=2_900_000, capex=1_800_000),
        "FY2025": dict(revenue=44_100_000, cogs=30_300_000, employee_cost=4_600_000, other_expenses=3_700_000,
                       depreciation=1_050_000, interest=1_300_000, other_income=150_000, tax=880_000,
                       total_assets=35_400_000, total_liabilities=21_600_000, current_assets=20_600_000,
                       current_liabilities=13_900_000, inventory=7_900_000, receivables=9_300_000,
                       payables=6_100_000, cash=1_500_000, net_worth=13_800_000, long_term_debt=6_900_000,
                       short_term_debt=5_600_000, current_portion_ltd=1_600_000, fixed_assets=13_900_000,
                       cfo=3_300_000, capex=2_200_000),
        "FY2026": dict(revenue=49_600_000, cogs=34_600_000, employee_cost=5_100_000, other_expenses=4_300_000,
                       depreciation=1_200_000, interest=1_600_000, other_income=140_000, tax=760_000,
                       total_assets=40_200_000, total_liabilities=24_400_000, current_assets=24_100_000,
                       current_liabilities=16_200_000, inventory=9_100_000, receivables=12_400_000,
                       payables=6_600_000, cash=1_200_000, net_worth=15_800_000, long_term_debt=8_100_000,
                       short_term_debt=6_900_000, current_portion_ltd=1_900_000, fixed_assets=15_200_000,
                       cfo=2_100_000, capex=2_600_000),
    }}
    (d / "financials_fy24-26.json").write_text(json.dumps(fin, indent=2))

    def plan(y, m):
        base = 4_300_000 * random.uniform(0.9, 1.1)
        return [
            (3, "RTGS CR-ANCHOR OEM MOTORS LTD", 0, round(base * 0.48, 2)),
            (8, "NEFT CR-DELTA TRACTORS PVT LTD", 0, round(base * 0.21, 2)),
            (12, "NEFT CR-VARIOUS CUSTOMERS", 0, round(base * 0.31, 2)),
            (5, "NACH DR-TERM LOAN EMI EXAMPLE BANK", 160_000, 0),
            (7, "RTGS DR-STEEL SUPPLIER PVT LTD", round(base * 0.55, 2), 0),
            (10, "SALARIES PAYROLL TRF", 420_000, 0),
            (20, "GST PAYMENT CBIC", round(base * 0.04, 2), 0),
            (24, "NEFT DR-POWER BESCOM ELECTRICITY", 95_000, 0),
            (26, "RTGS DR-MACHINING JOBWORK", round(base * 0.18, 2), 0),
        ]
    write_csv(d / "bank_statement_oct25-sep26.csv", bank_rows(date(2025, 10, 1), 12, 1_450_000.0, plan))


if __name__ == "__main__":
    retail_clean()
    retail_risky()
    sme()
    print(f"Samples written to {OUT}  (Aadhaar {AADHAAR[:4]}XXXX..., GSTIN {GSTIN})")
