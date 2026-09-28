"""
Indian Salary Structure (CTC) -> Monthly Salary Breakdown calculator.

This module converts an annual Cost-to-Company (CTC) figure into a monthly
salary structure compliant with EPF and ESI, and derives the employee's
monthly take-home figure.

STATUTORY BASIS
---------------
EPF  - Employees' Provident Fund, 12% employee + 12% employer on "basic wages"
       (basic + DA). Wage ceiling INR 25,000/month, raised from INR 15,000 by
       the Union Cabinet on 16-Sep-2026 and notified by the Ministry of Labour &
       Employment via Gazette Notification S.O. 5109(E) dated 17-Sep-2026.
       Source: PIB PRID 2313829 / Ministry of Labour & Employment press release.
ESI  - Employees' State Insurance, 0.75% employee + 3.25% employer on gross
       wages. Coverage ceiling INR 21,000/month (INR 25,000 for persons with
       disabilities), last revised w.e.f. 01-Jan-2017. Contribution rates
       (0.75% / 3.25%) unchanged since the revision effective 01-Jul-2019.
       Source: esic.gov.in / contribution page.
       Employees whose average daily wages are up to INR 176 are exempt from
       the *employee share* only; the employer share remains payable.

THE CTC ACCOUNTING IDENTITY
---------------------------
CTC is not gross salary. Employer statutory contributions are additional cost
borne by the employer and form part of CTC, so:

    Monthly CTC = Basic + HRA + Special Allowance + Employer EPF + Employer ESI
                = Gross Monthly + Employer EPF + Employer ESI

Special Allowance is therefore derived as a residual, which guarantees the
identity holds exactly rather than approximately.

ESI SOLVED SIMULTANEOUSLY
------------------------
When ESI applies, the employer's ESI contribution sits *inside* CTC, so gross
wages cannot be known before ESI is known (and vice-versa). Both candidate
structures are therefore solved in closed form:

    gross_without_esi = CTC_monthly - EPF_employer
    gross_with_esi    = gross_without_esi / 1.0325

Which one is correct depends on which gross is tested against the ESI ceiling,
and for an annual CTC of roughly INR 26,81,000 to INR 27,68,000 BOTH are lawful
(a bistable system). This is exposed as `esi_coverage_rule` rather than being
baked in - see `ESI_COVERAGE_STRICT` / `ESI_COVERAGE_CONSERVATIVE`.

SCOPE LIMITATION - READ THIS
----------------------------
`net_monthly` is Gross - Employee EPF - Employee ESI. It is NOT an actual bank
credit. It excludes income-tax TDS, professional tax, and any other voluntary
deductions. Use `format_breakdown` output's "NOT INCLUDED" section for the full
list. Treating this value as real take-home will overstate it materially at
higher CTC levels.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

__all__ = [
    "SalaryBreakdown",
    "calculate_breakdown",
    "format_breakdown",
    "BASIC_PCT_OF_CTC",
    "HRA_PCT_OF_BASIC",
    "EPF_RATE",
    "EPF_WAGE_CEILING",
    "ESI_EMPLOYEE_RATE",
    "ESI_EMPLOYER_RATE",
    "ESI_WAGE_CEILING",
    "ESI_COVERAGE_STRICT",
    "ESI_COVERAGE_CONSERVATIVE",
    "ESI_COVERAGE_RULES",
]

# --------------------------------------------------------------------------
# Statutory constants (Decimals - binary floats are unacceptable for payroll)
# --------------------------------------------------------------------------

PAISE = Decimal("0.01")
ZERO = Decimal("0.00")
MONTHS_PER_YEAR = 12

#: Salary structuring norms (employer policy, not statute - both are configurable).
BASIC_PCT_OF_CTC = Decimal("50")      # Basic = 50% of monthly CTC
HRA_PCT_OF_BASIC = Decimal("50")      # HRA  = 50% of Basic

# --- Provident Fund -------------------------------------------------------
EPF_RATE = Decimal("12")                       # % of basic wages, each side
EPF_WAGE_CEILING = Decimal("25000")            # INR/month, w.e.f. 17-Sep-2026

# --- Employees' State Insurance -------------------------------------------
ESI_EMPLOYEE_RATE = Decimal("0.75")            # % of gross wages
ESI_EMPLOYER_RATE = Decimal("3.25")            # % of gross wages
ESI_WAGE_CEILING = Decimal("21000")            # INR/month gross, w.e.f. 01-Jan-2017
ESI_EMPLOYEE_EXEMPT_DAILY_WAGE = Decimal("176")  # employee share only
DAYS_PER_MONTH = Decimal("30")                 # ESIC convention for daily wage

# --- ESI coverage tie-breaking --------------------------------------------
# The ESI threshold is a test on *gross wages*, but gross wages here are derived
# from CTC, and employer ESI sits inside CTC. That makes the system bistable:
# for an annual CTC of roughly INR 26,81,000 to INR 27,68,000 there are two
# self-consistent structures, e.g. at CTC 2,70,000:
#
#     (A) gross 21,166.79, ESI nil        - "ESI does not apply"
#     (B) gross 20,502.07, ESI 664.72     - "ESI applies, and 20,502 <= 21,000"
#
# Both satisfy every statutory rule. The law does not pick a winner, so this is
# surfaced as an explicit choice rather than hidden in the arithmetic.

#: Forward-looking / operational. Derive gross from CTC first, then test it
#: against INR 21,000. This is how payroll systems actually compute - gross is
#: struck, deductions follow. Yields the higher gross and the higher take-home.
ESI_COVERAGE_STRICT = "strict"

#: Statutorily conservative. Assume ESI applies, and keep it if the resulting
#: gross still lands at or below INR 21,000. Guarantees ESI coverage is never
#: withheld where it could validly apply.
ESI_COVERAGE_CONSERVATIVE = "conservative"

ESI_COVERAGE_RULES = (ESI_COVERAGE_STRICT, ESI_COVERAGE_CONSERVATIVE)

# Windows consoles frequently default to a legacy code page that cannot encode
# the rupee sign; degrade to "Rs." rather than crashing on print.
try:
    "₹".encode(sys.stdout.encoding or "utf-8")
    RUPEE = "₹"
except (UnicodeEncodeError, LookupError):
    RUPEE = "Rs. "


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _to_decimal(value, field_name: str) -> Decimal:
    """Coerce user input to Decimal without ever going through binary float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # Re-route floats through their shortest repr so 180000.0 -> "180000.0"
        # rather than the full 17-significant-digit binary expansion.
        value = repr(value)
    try:
        parsed = Decimal(str(value).strip().replace(",", "").replace("_", ""))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be a positive number, got {value!r}"
        ) from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return parsed


def q2(value: Decimal) -> Decimal:
    """Quantise to 2 decimal places, half-up (Indian payroll rounding)."""
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


def _pct(amount: Decimal, percent: Decimal) -> Decimal:
    """Return `percent`% of `amount`, rounded half-up to the paisa."""
    return q2(amount * percent / Decimal(100))


def _money(value: Decimal) -> str:
    """Thousands-separated amount, e.g. 291666.67 -> '2,91,666.67'."""
    return f"{value:,.2f}"


# --------------------------------------------------------------------------
# Result container
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SalaryBreakdown:
    """Monthly salary structure for one employee. All money fields are Decimal."""

    annual_ctc: Decimal
    monthly_ctc: Decimal

    # Gross components (paid to the employee)
    basic: Decimal
    hra: Decimal
    special_allowance: Decimal
    gross_monthly: Decimal

    # Employer-borne statutory costs (part of CTC, not paid to employee)
    epf_wage_base: Decimal
    epf_employer: Decimal
    esi_employer: Decimal

    # Employee-borne deductions
    epf_employee: Decimal
    esi_employee: Decimal

    net_monthly: Decimal

    # Diagnostics - explains *why* the numbers came out as they did
    epf_ceiling_applied: bool
    esi_applicable: bool
    esi_employee_exempt: bool
    gross_without_esi: Decimal
    esi_coverage_rule: str
    esi_rule_ambiguous: bool

    # --- Derived annual figures -------------------------------------------
    @property
    def annual_gross(self) -> Decimal:
        return q2(self.gross_monthly * MONTHS_PER_YEAR)

    @property
    def annual_net(self) -> Decimal:
        return q2(self.net_monthly * MONTHS_PER_YEAR)

    @property
    def employee_deductions_monthly(self) -> Decimal:
        return q2(self.epf_employee + self.esi_employee)

    @property
    def employer_statutory_monthly(self) -> Decimal:
        return q2(self.epf_employer + self.esi_employer)

    @property
    def annual_employer_statutory(self) -> Decimal:
        return q2(self.employer_statutory_monthly * MONTHS_PER_YEAR)

    def ctc_reconciliation_error(self) -> Decimal:
        """
        Residual between CTC and the sum of its parts. Must be exactly zero:
        the special allowance is computed as a residual specifically so that
        no paise leak out of the structure during rounding.
        """
        parts = (
            self.basic
            + self.hra
            + self.special_allowance
            + self.epf_employer
            + self.esi_employer
        )
        return q2(self.monthly_ctc - parts)

    def to_dict(self) -> dict:
        """JSON-ready dict of every field, including derived ones."""
        data = {k: str(v) if isinstance(v, Decimal) else v
                for k, v in asdict(self).items()}
        data.update(
            annual_gross=str(self.annual_gross),
            annual_net=str(self.annual_net),
            monthly_ctc_reconciliation_error=str(self.ctc_reconciliation_error()),
        )
        return data


# --------------------------------------------------------------------------
# Core calculation
# --------------------------------------------------------------------------

def calculate_breakdown(
    annual_ctc,
    *,
    basic_pct=BASIC_PCT_OF_CTC,
    hra_pct_of_basic=HRA_PCT_OF_BASIC,
    esi_coverage_rule=ESI_COVERAGE_STRICT,
    esi_employee_exempt: bool = True,
    days_per_month=DAYS_PER_MONTH,
) -> SalaryBreakdown:
    """
    Split an annual CTC into a compliant monthly salary structure.

    Args:
        annual_ctc: Annual cost-to-company in INR. Accepts int, float, str or
            Decimal; commas and underscores are tolerated.
        basic_pct: Basic as a percentage of monthly CTC. Defaults to 50, the
            common industry norm. Note that lowering this reduces both the EPF
            wage base and the HRA base, which is the main lever employers use
            to control statutory cost.
        hra_pct_of_basic: HRA as a percentage of Basic. Defaults to 50.
        esi_coverage_rule: Which fixed point to select when the ESI threshold
            admits more than one answer. See :data:`ESI_COVERAGE_STRICT` and
            :data:`ESI_COVERAGE_CONSERVATIVE`. Defaults to
            :data:`ESI_COVERAGE_STRICT`.
        esi_employee_exempt: When True (default) employees whose average daily
            wages are up to INR 176 are exempted from the 0.75% employee ESI
            share. Set False to apply a flat 0.75% to every ESI-covered
            employee regardless of wage level.
        days_per_month: Divisor used to convert monthly wages into the average
            daily wage for the INR 176 exemption test. ESIC's convention is 30.

    Returns:
        A populated :class:`SalaryBreakdown`.

    Raises:
        ValueError: If the CTC is non-positive/non-numeric, if the structure
            parameters fall outside 0-100, or if the resulting structure would
            drive special allowance negative (see below).
    """
    ctc = q2(_to_decimal(annual_ctc, "annual_ctc"))
    if ctc <= 0:
        raise ValueError("annual_ctc must be greater than zero")

    basic_pct = _to_decimal(basic_pct, "basic_pct")
    hra_pct = _to_decimal(hra_pct_of_basic, "hra_pct_of_basic")
    days = _to_decimal(days_per_month, "days_per_month")
    if not (0 <= basic_pct <= 100):
        raise ValueError("basic_pct must be between 0 and 100")
    if not (0 <= hra_pct <= 200):
        raise ValueError("hra_pct_of_basic must be between 0 and 200")
    if days <= 0:
        raise ValueError("days_per_month must be greater than zero")

    # --- Step 1: gross salary components ---------------------------------
    monthly_ctc = q2(ctc / MONTHS_PER_YEAR)
    basic = _pct(monthly_ctc, basic_pct)
    hra = _pct(basic, hra_pct)

    # --- Step 2: EPF on basic wages, subject to the statutory ceiling -----
    epf_wage_base = q2(min(basic, EPF_WAGE_CEILING))
    epf_employer = _pct(epf_wage_base, EPF_RATE)
    # Employer and employee EPF are each a flat 12% of the same base. (Voluntary
    # provident fund above 12% and the 8.33% EPS/3.67% EPF split are not
    # modelled here - see README.)
    epf_employee = epf_employer

    # --- Step 3: resolve ESI coverage and gross wages --------------------
    # Employer ESI is itself part of CTC, so it reduces gross wages, which in
    # turn reduces ESI. Solving rather than iterating gives two candidate
    # structures:
    #     gross_without_esi = CTC - EPF(er)              and ESI(er) = 0
    #     gross_with_esi    = gross_without_esi / 1.0325 and ESI(er) = the rest
    gross_without_esi = monthly_ctc - epf_employer
    gross_with_esi = q2(
        gross_without_esi / (Decimal(1) + ESI_EMPLOYER_RATE / Decimal(100))
    )

    if esi_coverage_rule not in ESI_COVERAGE_RULES:
        raise ValueError(
            f"esi_coverage_rule must be one of {ESI_COVERAGE_RULES}, "
            f"got {esi_coverage_rule!r}"
        )

    if esi_coverage_rule == ESI_COVERAGE_CONSERVATIVE:
        # Keep ESI whenever the ESI-bearing structure still clears the ceiling.
        esi_applicable = gross_with_esi <= ESI_WAGE_CEILING
    else:
        # Forward-looking: test the gross implied by the CTC before any ESI.
        esi_applicable = gross_without_esi <= ESI_WAGE_CEILING

    if esi_applicable:
        gross_monthly = gross_with_esi
        # Taking employer ESI as the residual keeps the CTC identity exact to
        # the paisa, rather than drifting by a rounding unit.
        esi_employer = gross_without_esi - gross_monthly
    else:
        gross_monthly = gross_without_esi
        esi_employer = ZERO

    # Both structures are legally valid when the CTC sits in the band where
    # gross_without_esi is above the ceiling but gross_with_esi is not. Recorded
    # so an auditor can see the result was a judgement call, not an accident.
    esi_rule_ambiguous = (
        gross_without_esi > ESI_WAGE_CEILING >= gross_with_esi
    )

    # --- Step 4: special allowance is the balancing figure ---------------
    special_allowance = gross_monthly - basic - hra
    if special_allowance < 0:
        raise ValueError(
            f"basic_pct={basic_pct}% and hra_pct_of_basic={hra_pct}% leave no room "
            f"for special allowance at a CTC of {ctc}. Reduce basic_pct or "
            f"hra_pct_of_basic."
        )

    # --- Step 5: employee ESI, including the low-wage exemption ----------
    esi_employee_exempt_applies = False
    if esi_applicable:
        average_daily_wage = q2(gross_monthly / days)
        if esi_employee_exempt and average_daily_wage <= ESI_EMPLOYEE_EXEMPT_DAILY_WAGE:
            esi_employee = ZERO
            esi_employee_exempt_applies = True
        else:
            esi_employee = _pct(gross_monthly, ESI_EMPLOYEE_RATE)
    else:
        esi_employee = ZERO

    # --- Step 6: take-home ------------------------------------------------
    net_monthly = q2(gross_monthly - epf_employee - esi_employee)

    return SalaryBreakdown(
        annual_ctc=ctc,
        monthly_ctc=monthly_ctc,
        basic=basic,
        hra=hra,
        special_allowance=special_allowance,
        gross_monthly=gross_monthly,
        epf_wage_base=epf_wage_base,
        epf_employer=epf_employer,
        esi_employer=esi_employer,
        epf_employee=epf_employee,
        esi_employee=esi_employee,
        net_monthly=net_monthly,
        epf_ceiling_applied=basic > EPF_WAGE_CEILING,
        esi_applicable=esi_applicable,
        esi_employee_exempt=esi_employee_exempt_applies,
        gross_without_esi=gross_without_esi,
        esi_coverage_rule=esi_coverage_rule,
        esi_rule_ambiguous=esi_rule_ambiguous,
    )


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------

def format_breakdown(b: SalaryBreakdown) -> str:
    """Render a breakdown as a human-readable, aligned report."""
    R, W = RUPEE, 46
    line = "-" * (W + 34)
    out: list[str] = []

    def row(label: str, amount: Decimal, note: str = "") -> str:
        left = f"  {label:<{W - 2}}"
        right = f"{R}{_money(amount):>12}"
        if note:
            return f"{left}{right}   {note}"
        return f"{left}{right}"

    out.append("=" * (W + 34))
    out.append(f"{'INDIAN SALARY BREAKDOWN':<{W}}Monthly (INR)")
    out.append("=" * (W + 34))
    out.append(row("Annual CTC", b.annual_ctc))
    out.append(row("Monthly CTC", b.monthly_ctc))
    out.append("")

    out.append("GROSS SALARY COMPONENTS")
    out.append(line)
    out.append(row("Basic Salary", b.basic, "50% of CTC"))
    out.append(row("House Rent Allowance (HRA)", b.hra, "50% of Basic"))
    out.append(row("Special Allowance", b.special_allowance, "balancing figure"))
    out.append(row("GROSS MONTHLY SALARY", b.gross_monthly))
    out.append("")

    out.append("EMPLOYER CONTRIBUTIONS (part of CTC, not paid out)")
    out.append(line)
    pf_note = f"12% of {R}{_money(b.epf_wage_base)}"
    if b.epf_ceiling_applied:
        pf_note += f" [capped at {R}25,000 ceiling]"
    out.append(row("Employer EPF", b.epf_employer, pf_note))

    if b.esi_applicable:
        out.append(row("Employer ESI", b.esi_employer, "3.25% of gross"))
    else:
        out.append(row("Employer ESI", b.esi_employer,
                       "not applicable - gross > " + R + "21,000"))
    out.append(row("Total Employer Statutory", b.employer_statutory_monthly))
    if b.esi_rule_ambiguous:
        out.append(
            "  * NOTE: this CTC falls in the ESI bistable band "
            + R + "2,68,085 - " + R + "2,76,798 p.a., where two structures are both"
        )
        out.append(
            "    lawful. Showing the '" + b.esi_coverage_rule + "' rule. Run with"
        )
        out.append(
            "    --esi-coverage conservative for the alternative. Confirm with payroll."
        )
    out.append("")

    out.append("EMPLOYEE DEDUCTIONS")
    out.append(line)
    out.append(row("Employee EPF", b.epf_employee, "12% of basic wages"))
    if b.esi_employee_exempt:
        out.append(row("Employee ESI", b.esi_employee,
                       "EXEMPT - avg daily wage <= " + R + "176"))
    elif b.esi_applicable:
        out.append(row("Employee ESI", b.esi_employee, "0.75% of gross"))
    else:
        out.append(row("Employee ESI", b.esi_employee, "not applicable"))
    out.append(row("Total Employee Deductions", b.employee_deductions_monthly))
    out.append("")

    out.append("TAKE-HOME")
    out.append(line)
    out.append(row("NET MONTHLY SALARY", b.net_monthly))
    out.append(row("Net Annual Salary", b.annual_net))
    out.append("")

    out.append("ANNUAL VIEW")
    out.append(line)
    out.append(row("Gross Annual Salary", b.annual_gross))
    out.append(row("Employee EPF + ESI", q2(b.employee_deductions_monthly * 12)))
    out.append(row("Net Annual Salary", b.annual_net))
    out.append(row("Employer Statutory (annual)", b.annual_employer_statutory))
    out.append("")

    out.append("NOT INCLUDED IN 'NET MONTHLY SALARY' ABOVE")
    out.append(line)
    for item in (
        "Income tax TDS (new regime: " + R + "75,000 standard deduction, nil tax up to " + R + "12.75L gross)",
        "Professional tax (state levy, typically " + R + "175-" + R + "200/month)",
        "Voluntary PF above 12% / VPF, or the 8.33% EPS + 3.67% EPF split",
        "Gratuity, group insurance and other employer CTC costs not modelled here",
        "Any other voluntary deductions (advances, recovery, canteen, etc.)",
    ):
        out.append(f"  - {item}")

    err = b.ctc_reconciliation_error()
    out.append("")
    out.append(f"CTC reconciliation: monthly CTC - components = {R}{_money(err)}")
    out.append("=" * (W + 34))
    return "\n".join(out)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="salary_breakdown",
        description=(
            "Convert an annual Indian CTC into a monthly salary structure with "
            "EPF and ESI."
        ),
        epilog=(
            "examples:\n"
            "  salary_breakdown.py 1200000\n"
            "  salary_breakdown.py \"12,00,000\"\n"
            "  salary_breakdown.py 180000 --json\n"
            "  salary_breakdown.py 270000 --esi-coverage conservative\n"
            f"\nStatutory basis: EPF 12%/12% on basic wages capped at {RUPEE}25,000/month "
            "(Gazette S.O. 5109(E), 17-Sep-2026); ESI 0.75%/3.25% on gross wages "
            f"up to {RUPEE}21,000/month."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ctc", nargs="?", help="Annual CTC, e.g. 1200000 or 12,00,000")
    parser.add_argument("--ctc", dest="ctc_opt", help="Annual CTC (alternative to positional)")
    parser.add_argument(
        "--basic-pct", type=Decimal, default=BASIC_PCT_OF_CTC,
        help=f"Basic as a percentage of monthly CTC (default: {BASIC_PCT_OF_CTC})",
    )
    parser.add_argument(
        "--hra-pct-of-basic", type=Decimal, default=HRA_PCT_OF_BASIC,
        help=f"HRA as a percentage of Basic (default: {HRA_PCT_OF_BASIC})",
    )
    parser.add_argument(
        "--esi-coverage", choices=ESI_COVERAGE_RULES, default=ESI_COVERAGE_STRICT,
        help=(
            "How to break the ESI threshold tie where two structures are both "
            "lawful (CTC %s2,68,085 - %s2,76,798 p.a.). 'strict' (default) derives "
            "gross first, then tests it - the higher gross and take-home. "
            "'conservative' keeps ESI whenever the ESI-bearing gross still "
            "clears %s21,000." % (RUPEE, RUPEE, RUPEE)
        ),
    )
    parser.add_argument(
        "--no-esi-employee-exemption", action="store_true",
        help="Charge the flat 0.75%% ESI employee share to every covered employee",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")

    args = parser.parse_args(argv)
    ctc = args.ctc_opt or args.ctc
    if ctc is None:
        parser.error("an annual CTC is required (positional or --ctc)")

    try:
        result = calculate_breakdown(
            ctc,
            basic_pct=args.basic_pct,
            hra_pct_of_basic=args.hra_pct_of_basic,
            esi_coverage_rule=args.esi_coverage,
            esi_employee_exempt=not args.no_esi_employee_exemption,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result.to_dict(), indent=2) if args.json else format_breakdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
