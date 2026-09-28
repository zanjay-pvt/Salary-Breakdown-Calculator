"""
Pre-calculated test suite for salary_breakdown.py.

Run with:
    python -m unittest -v
    python -m unittest test_salary_breakdown -v

Every expected value below was derived by hand from the statutory formulas and
independently cross-checked against the implementation. They are pinned to the
exact paisa so that any future refactor that changes a rupee fails loudly.

Coverage:
  * The three headline salary levels requested: low, mid-tier, higher professional
  * The very-low band where ESI triggers and the employee share is exempt
  * The INR 21,000 ESI ceiling, tested exactly on the boundary and just above it
  * The INR 25,000 EPF ceiling, tested at the kink and where it binds
  * Structural invariants across a wide sweep of CTC values
"""

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

# Allow `python -m unittest test_salary_breakdown` from any working directory,
# not just the project root - unittest only puts the CWD on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from salary_breakdown import (
    ESI_COVERAGE_CONSERVATIVE,
    ESI_COVERAGE_STRICT,
    ESI_WAGE_CEILING,
    EPF_WAGE_CEILING,
    SalaryBreakdown,
    calculate_breakdown,
    format_breakdown,
    main,
)

D = Decimal


# ==========================================================================
# 1. Pre-calculated cases
# ==========================================================================
#
# Each entry is (label, annual_ctc, {field: expected Decimal}, {flags}).
#
# Case B  - LOW CTC, INR 1.80 LPA. ESI is triggered.
#           monthly CTC            = 1,80,000 / 12          =  15,000.00
#           basic    = 50% of CTC                        =   7,500.00
#           HRA      = 50% of basic                      =   3,750.00
#           PF base  = min(7,500, 25,000)                =   7,500.00
#           EPF      = 12% x 7,500                       =     900.00
#           gross before ESI = 15,000 - 900              =  14,100.00  <= 21,000 -> ESI
#           gross    = 14,100 / 1.0325                   =  13,656.17
#           ESI(er)  = 14,100 - 13,656.17 (residual)     =     443.83
#           special  = 13,656.17 - 7,500 - 3,750         =   2,406.17
#           ESI(ee)  = 0.75% x 13,656.17                 =     102.42
#           net      = 13,656.17 - 900 - 102.42          =  12,653.75
#
# Case A  - VERY LOW CTC, INR 69,000. ESI triggers and the employee share is
#           exempt: average daily wage = 5,234.87 / 30 = 174.50 <= 176.
#           net = 5,234.87 - 345 - 0 = 4,889.87
#
# Case G  - MID-TIER, INR 12 LPA. PF ceiling binds hard (basic 50,000 > 25,000).
#           gross = 1,00,000 - 3,000 = 97,000 -> no ESI
#           net   = 97,000 - 3,000 = 94,000
#
# Case H  - HIGHER PROFESSIONAL, INR 35 LPA. Same capped PF of 3,000.
#           monthly CTC = 3,50,00,000 / 12 = 2,91,666.67 (half-up)
#           basic = 1,45,833.335 -> 1,45,833.34 (half-up)
#           HRA   =    72,916.67
#           gross = 2,91,666.67 - 3,000 = 2,88,666.67
#           net   = 2,85,666.67
#
PRE_CALCULATED_CASES = [
    (
        "A: very low CTC 69,000 - ESI applies, employee share exempt",
        D("69000"),
        {
            "monthly_ctc": D("5750.00"),
            "basic": D("2875.00"),
            "hra": D("1437.50"),
            "special_allowance": D("922.37"),
            "gross_monthly": D("5234.87"),
            "epf_wage_base": D("2875.00"),
            "epf_employer": D("345.00"),
            "esi_employer": D("170.13"),
            "epf_employee": D("345.00"),
            "esi_employee": D("0.00"),
            "net_monthly": D("4889.87"),
            "gross_without_esi": D("5405.00"),
            "annual_net": D("58678.44"),
        },
        {"esi_applicable": True, "esi_employee_exempt": True,
         "epf_ceiling_applied": False},
    ),
    (
        "B: low CTC 1,80,000 - ESI triggered",
        D("180000"),
        {
            "monthly_ctc": D("15000.00"),
            "basic": D("7500.00"),
            "hra": D("3750.00"),
            "special_allowance": D("2406.17"),
            "gross_monthly": D("13656.17"),
            "epf_wage_base": D("7500.00"),
            "epf_employer": D("900.00"),
            "esi_employer": D("443.83"),
            "epf_employee": D("900.00"),
            "esi_employee": D("102.42"),
            "net_monthly": D("12653.75"),
            "gross_without_esi": D("14100.00"),
            "annual_net": D("151845.00"),
        },
        {"esi_applicable": True, "esi_employee_exempt": False,
         "epf_ceiling_applied": False},
    ),
    (
        "C: ESI boundary, exactly at 21,000 gross (inclusive)",
        D("268085.16"),
        {
            "monthly_ctc": D("22340.43"),
            "basic": D("11170.22"),
            "hra": D("5585.11"),
            "special_allowance": D("3583.65"),
            "gross_monthly": D("20338.98"),
            "epf_wage_base": D("11170.22"),
            "epf_employer": D("1340.43"),
            "esi_employer": D("661.02"),
            "epf_employee": D("1340.43"),
            "esi_employee": D("152.54"),
            "net_monthly": D("18846.01"),
            "gross_without_esi": D("21000.00"),
        },
        {"esi_applicable": True, "epf_ceiling_applied": False},
    ),
    (
        "D: ESI boundary, first step above 21,000 gross (strict rule)",
        D("268120.00"),
        {
            "monthly_ctc": D("22343.33"),
            "basic": D("11171.67"),
            "hra": D("5585.84"),
            "special_allowance": D("4245.22"),
            "gross_monthly": D("21002.73"),
            "epf_wage_base": D("11171.67"),
            "epf_employer": D("1340.60"),
            "esi_employer": D("0.00"),
            "epf_employee": D("1340.60"),
            "esi_employee": D("0.00"),
            "net_monthly": D("19662.13"),
            "gross_without_esi": D("21002.73"),
        },
        # Sits inside the bistable band: the conservative rule would keep ESI here
        {"esi_applicable": False, "epf_ceiling_applied": False,
         "esi_rule_ambiguous": True},
    ),
    (
        "E: EPF ceiling kink - basic exactly 25,000",
        D("600000"),
        {
            "monthly_ctc": D("50000.00"),
            "basic": D("25000.00"),
            "hra": D("12500.00"),
            "special_allowance": D("9500.00"),
            "gross_monthly": D("47000.00"),
            "epf_wage_base": D("25000.00"),
            "epf_employer": D("3000.00"),
            "epf_employee": D("3000.00"),
            "net_monthly": D("44000.00"),
            "annual_net": D("528000.00"),
        },
        # basic == ceiling, so the cap does not actually reduce anything
        {"esi_applicable": False, "epf_ceiling_applied": False},
    ),
    (
        "F: mid-tier 9,00,000 - PF ceiling binds",
        D("900000"),
        {
            "monthly_ctc": D("75000.00"),
            "basic": D("37500.00"),
            "hra": D("18750.00"),
            "special_allowance": D("15750.00"),
            "gross_monthly": D("72000.00"),
            "epf_wage_base": D("25000.00"),
            "epf_employer": D("3000.00"),
            "epf_employee": D("3000.00"),
            "net_monthly": D("69000.00"),
            "annual_net": D("828000.00"),
        },
        {"esi_applicable": False, "epf_ceiling_applied": True},
    ),
    (
        "G: mid-tier 12,00,000",
        D("1200000"),
        {
            "monthly_ctc": D("100000.00"),
            "basic": D("50000.00"),
            "hra": D("25000.00"),
            "special_allowance": D("22000.00"),
            "gross_monthly": D("97000.00"),
            "epf_wage_base": D("25000.00"),
            "epf_employer": D("3000.00"),
            "esi_employer": D("0.00"),
            "epf_employee": D("3000.00"),
            "esi_employee": D("0.00"),
            "net_monthly": D("94000.00"),
            "gross_without_esi": D("97000.00"),
            "annual_net": D("1128000.00"),
        },
        {"esi_applicable": False, "epf_ceiling_applied": True},
    ),
    (
        "H: higher professional 35,00,000",
        D("3500000"),
        {
            "monthly_ctc": D("291666.67"),
            "basic": D("145833.34"),
            "hra": D("72916.67"),
            "special_allowance": D("69916.66"),
            "gross_monthly": D("288666.67"),
            "epf_wage_base": D("25000.00"),
            "epf_employer": D("3000.00"),
            "epf_employee": D("3000.00"),
            "net_monthly": D("285666.67"),
            "annual_net": D("3428000.04"),
        },
        {"esi_applicable": False, "epf_ceiling_applied": True},
    ),
]


class TestPreCalculatedCases(unittest.TestCase):
    """Pin every output field to a hand-computed value for the headline cases."""

    def test_all_fields_match_precalculated_values(self):
        for label, ctc, expected, flags in PRE_CALCULATED_CASES:
            with self.subTest(case=label):
                b = calculate_breakdown(ctc)
                for field, want in expected.items():
                    got = getattr(b, field)
                    self.assertEqual(
                        got, want,
                        f"{label}: {field} expected {want}, got {got}",
                    )
                for field, want in flags.items():
                    got = getattr(b, field)
                    self.assertIs(
                        got, want, f"{label}: {field} expected {want}, got {got}"
                    )

    def test_ctc_identity_holds_for_every_case(self):
        """basic + HRA + special + EPF(er) + ESI(er) must equal monthly CTC exactly."""
        for label, ctc, _, _ in PRE_CALCULATED_CASES:
            with self.subTest(case=label):
                b = calculate_breakdown(ctc)
                self.assertEqual(b.ctc_reconciliation_error(), D("0.00"))


# ==========================================================================
# 2. Statutory rule tests
# ==========================================================================

class TestEPFRules(unittest.TestCase):

    def test_epf_is_12_percent_of_basic_below_ceiling(self):
        b = calculate_breakdown(D("180000"))
        self.assertEqual(b.epf_wage_base, D("7500.00"))
        self.assertEqual(b.epf_employer, D("900.00"))   # 12% x 7,500
        self.assertEqual(b.epf_employee, D("900.00"))
        self.assertEqual(b.epf_employee, b.epf_employer)

    def test_epf_base_capped_at_25000_ceiling(self):
        b = calculate_breakdown(D("3500000"))
        self.assertGreater(b.basic, EPF_WAGE_CEILING)
        self.assertEqual(b.epf_wage_base, EPF_WAGE_CEILING)
        self.assertEqual(b.epf_employer, D("3000.00"))  # 12% x 25,000, not 12% x 1,45,833
        self.assertTrue(b.epf_ceiling_applied)

    def test_pf_ceiling_means_pf_does_not_scale_with_higher_ctc(self):
        """Above the ceiling, the PF contribution is flat at 3,000/month."""
        for ctc in ("900000", "1200000", "2400000", "6000000", "12000000"):
            with self.subTest(ctc=ctc):
                b = calculate_breakdown(D(ctc))
                self.assertEqual(b.epf_employer, D("3000.00"))

    def test_epf_ceiling_exact_25k_is_not_flagged(self):
        """At basic == 25,000 the cap binds but does not reduce the base."""
        b = calculate_breakdown(D("600000"))
        self.assertEqual(b.basic, EPF_WAGE_CEILING)
        self.assertEqual(b.epf_wage_base, EPF_WAGE_CEILING)
        self.assertFalse(b.epf_ceiling_applied)


class TestESIRules(unittest.TestCase):

    def test_esi_applies_below_21000_gross(self):
        b = calculate_breakdown(D("180000"))
        self.assertLessEqual(b.gross_monthly, ESI_WAGE_CEILING)
        self.assertTrue(b.esi_applicable)
        self.assertEqual(b.esi_employer, D("443.83"))
        self.assertEqual(b.esi_employee, D("102.42"))

    def test_esi_ceiling_is_inclusive_at_exactly_21000(self):
        """gross_without_esi == 21,000.00 must still attract ESI."""
        b = calculate_breakdown(D("268085.16"))
        self.assertEqual(b.gross_without_esi, ESI_WAGE_CEILING)
        self.assertTrue(b.esi_applicable)
        self.assertGreater(b.esi_employer, D("0.00"))
        self.assertGreater(b.esi_employee, D("0.00"))

    def test_esi_drops_to_zero_just_above_21000(self):
        b = calculate_breakdown(D("268120"))
        self.assertGreater(b.gross_monthly, ESI_WAGE_CEILING)
        self.assertFalse(b.esi_applicable)
        self.assertEqual(b.esi_employer, D("0.00"))
        self.assertEqual(b.esi_employee, D("0.00"))

    def test_employer_plus_employee_esi_is_four_percent_of_gross(self):
        """0.75% + 3.25% = 4.00% combined."""
        for ctc in ("180000", "240000", "268085.16"):
            with self.subTest(ctc=ctc):
                b = calculate_breakdown(D(ctc))
                combined = D("0.04") * b.gross_monthly
                self.assertLessEqual(
                    abs((b.esi_employer + b.esi_employee) - combined), D("0.01")
                )

    def test_employer_esi_is_exactly_325_percent_of_gross(self):
        b = calculate_breakdown(D("180000"))
        self.assertLessEqual(
            abs(b.esi_employer - D("0.0325") * b.gross_monthly), D("0.01")
        )

    def test_low_wage_employee_share_exempt_upto_176_per_day(self):
        """Average daily wage <= 176 exempts the employee share only."""
        b = calculate_breakdown(D("69000"))
        daily = b.gross_monthly / D("30")
        self.assertLessEqual(daily, D("176"))
        self.assertEqual(b.esi_employee, D("0.00"))
        self.assertGreater(b.esi_employer, D("0.00"))   # employer still pays
        self.assertTrue(b.esi_employee_exempt)

    def test_exemption_stops_just_above_176_per_day(self):
        b = calculate_breakdown(D("69600"))            # gross 5,280.39 -> 176.01/day
        daily = b.gross_monthly / D("30")
        self.assertGreater(daily, D("176"))
        self.assertFalse(b.esi_employee_exempt)
        self.assertEqual(b.esi_employee, D("39.60"))   # 0.75% of 5,280.39

    def test_exemption_can_be_disabled(self):
        b = calculate_breakdown(D("69000"), esi_employee_exempt=False)
        self.assertEqual(b.esi_employee, D("39.26"))   # 0.75% of 5,234.87
        self.assertEqual(b.net_monthly, D("4850.61"))
        self.assertEqual(b.esi_employer, D("170.13"))   # unchanged

    def test_esi_coverage_does_not_flicker_across_a_sweep(self):
        """Coverage must switch off once and stay off as CTC increases."""
        seen_inactive = False
        for paise in range(200000, 400000, 97):
            b = calculate_breakdown(D(paise) / D("100"))
            if not b.esi_applicable:
                seen_inactive = True
            elif seen_inactive:
                self.fail(f"ESI re-activated at CTC {paise / 100}")

    def test_esi_free_ctc_stays_esi_free_forever(self):
        for ctc in ("300000", "500000", "1000000", "5000000", "100000000"):
            with self.subTest(ctc=ctc):
                b = calculate_breakdown(D(ctc))
                self.assertFalse(b.esi_applicable)
                self.assertEqual(b.esi_employer, D("0.00"))
                self.assertEqual(b.esi_employee, D("0.00"))


class TestESICoverageRuleTieBreak(unittest.TestCase):
    """
    The ESI threshold is a test on gross, but gross is derived from CTC while
    employer ESI lives inside CTC - a bistable system. For an annual CTC of
    roughly 26,81,000 to 27,68,000 both structures are lawful, so the choice is
    made explicit rather than hidden in the arithmetic.
    """

    BAND = ("268085.22", "276797.93")

    def test_both_rules_agree_outside_the_band(self):
        for ctc in ("60000", "180000", "268085.16", "276800.00", "600000",
                    "1200000", "3500000"):
            with self.subTest(ctc=ctc):
                s = calculate_breakdown(D(ctc))
                c = calculate_breakdown(D(ctc), esi_coverage_rule=ESI_COVERAGE_CONSERVATIVE)
                self.assertEqual(s.gross_monthly, c.gross_monthly)
                self.assertEqual(s.esi_employer, c.esi_employer)
                self.assertEqual(s.net_monthly, c.net_monthly)
                self.assertFalse(s.esi_rule_ambiguous)

    def test_band_is_flagged_ambiguous_under_both_rules(self):
        lo, hi = self.BAND
        for ctc in (lo, "270000.00", hi):
            with self.subTest(ctc=ctc):
                for rule in (ESI_COVERAGE_STRICT, ESI_COVERAGE_CONSERVATIVE):
                    self.assertTrue(
                        calculate_breakdown(D(ctc), esi_coverage_rule=rule)
                        .esi_rule_ambiguous
                    )

    def test_strict_takes_the_higher_gross(self):
        b = calculate_breakdown(D("270000"))
        c = calculate_breakdown(D("270000"), esi_coverage_rule=ESI_COVERAGE_CONSERVATIVE)
        self.assertGreater(b.gross_monthly, c.gross_monthly)   # 21,150 vs 20,484
        self.assertEqual(b.esi_employer, D("0.00"))
        self.assertGreater(c.esi_employer, D("0.00"))

    def test_conservative_keeps_esi_inside_the_band(self):
        b = calculate_breakdown(D("270000"), esi_coverage_rule=ESI_COVERAGE_CONSERVATIVE)
        self.assertTrue(b.esi_applicable)
        self.assertLessEqual(b.gross_monthly, ESI_WAGE_CEILING)
        self.assertEqual(b.esi_employer, D("665.74"))
        self.assertEqual(b.esi_employee, D("153.63"))         # 0.75% x 20,484.26
        self.assertEqual(b.ctc_reconciliation_error(), D("0.00"))

    def test_conservative_boundary_is_inclusive_at_exactly_21000(self):
        """Top of the band: ESI-bearing gross lands on exactly 21,000.00."""
        b = calculate_breakdown(
            D("276797.93"), esi_coverage_rule=ESI_COVERAGE_CONSERVATIVE
        )
        self.assertEqual(b.gross_monthly, D("21000.00"))
        self.assertTrue(b.esi_applicable)
        self.assertEqual(b.esi_employer, D("682.50"))

    def test_both_rules_reconcile_inside_the_band(self):
        lo, hi = self.BAND
        for ctc in (lo, "270000", hi):
            for rule in (ESI_COVERAGE_STRICT, ESI_COVERAGE_CONSERVATIVE):
                with self.subTest(ctc=ctc, rule=rule):
                    self.assertEqual(
                        calculate_breakdown(D(ctc), esi_coverage_rule=rule)
                        .ctc_reconciliation_error(),
                        D("0.00"),
                    )

    def test_rule_is_recorded_on_the_result(self):
        self.assertEqual(
            calculate_breakdown(D("180000")).esi_coverage_rule, ESI_COVERAGE_STRICT
        )
        self.assertEqual(
            calculate_breakdown(D("180000"), esi_coverage_rule=ESI_COVERAGE_CONSERVATIVE)
            .esi_coverage_rule,
            ESI_COVERAGE_CONSERVATIVE,
        )

    def test_invalid_rule_rejected(self):
        with self.assertRaises(ValueError):
            calculate_breakdown(D("180000"), esi_coverage_rule="lenient")

    def test_report_warns_inside_the_band(self):
        self.assertIn("bistable", format_breakdown(calculate_breakdown(D("270000"))))
        self.assertNotIn("bistable", format_breakdown(calculate_breakdown(D("180000"))))


# ==========================================================================
# 3. Structural invariants
# ==========================================================================

class TestStructuralInvariants(unittest.TestCase):

    SWEEP = [D(x) for x in range(60000, 5000001, 11173)]

    def test_ctc_identity_holds_across_sweep(self):
        for ctc in self.SWEEP:
            with self.subTest(ctc=ctc):
                self.assertEqual(
                    calculate_breakdown(ctc).ctc_reconciliation_error(), D("0.00")
                )

    def test_ctc_identity_holds_under_conservative_rule(self):
        for ctc in self.SWEEP:
            with self.subTest(ctc=ctc):
                self.assertEqual(
                    calculate_breakdown(
                        ctc, esi_coverage_rule=ESI_COVERAGE_CONSERVATIVE
                    ).ctc_reconciliation_error(),
                    D("0.00"),
                )

    def test_conservative_never_exceeds_strict_take_home(self):
        """The conservative rule can only ever cost the employer, never gain."""
        for ctc in self.SWEEP:
            with self.subTest(ctc=ctc):
                strict = calculate_breakdown(ctc)
                conservative = calculate_breakdown(
                    ctc, esi_coverage_rule=ESI_COVERAGE_CONSERVATIVE
                )
                self.assertLessEqual(conservative.gross_monthly, strict.gross_monthly)
                self.assertLessEqual(conservative.net_monthly, strict.net_monthly)

    def test_no_negative_components_across_sweep(self):
        money_fields = (
            "basic", "hra", "special_allowance", "gross_monthly",
            "epf_wage_base", "epf_employer", "esi_employer",
            "epf_employee", "esi_employee", "net_monthly",
        )
        for ctc in self.SWEEP:
            b = calculate_breakdown(ctc)
            for field in money_fields:
                with self.subTest(ctc=ctc, field=field):
                    self.assertGreaterEqual(getattr(b, field), D("0.00"))

    def test_ordering_gross_gt_net_across_sweep(self):
        for ctc in self.SWEEP:
            with self.subTest(ctc=ctc):
                b = calculate_breakdown(ctc)
                self.assertGreater(b.gross_monthly, b.net_monthly)
                self.assertLessEqual(b.net_monthly, b.monthly_ctc)
                self.assertLessEqual(b.monthly_ctc, b.annual_ctc / D("12") + D("0.01"))

    def test_gross_equals_sum_of_components(self):
        for ctc in self.SWEEP:
            with self.subTest(ctc=ctc):
                b = calculate_breakdown(ctc)
                self.assertEqual(
                    b.basic + b.hra + b.special_allowance, b.gross_monthly
                )

    def test_net_equals_gross_less_deductions(self):
        for ctc in self.SWEEP:
            with self.subTest(ctc=ctc):
                b = calculate_breakdown(ctc)
                self.assertEqual(
                    b.gross_monthly - b.epf_employee - b.esi_employee, b.net_monthly
                )

    def test_pf_base_never_exceeds_ceiling(self):
        for ctc in self.SWEEP:
            with self.subTest(ctc=ctc):
                b = calculate_breakdown(ctc)
                self.assertLessEqual(b.epf_wage_base, EPF_WAGE_CEILING)
                self.assertLessEqual(b.epf_wage_base, b.basic)

    def test_no_binary_floats_leak_into_the_result(self):
        """Money serialises as a decimal string; flags stay as real booleans."""
        payload = calculate_breakdown(D("1234567.89")).to_dict()
        money_keys = (
            "annual_ctc", "monthly_ctc", "basic", "hra", "special_allowance",
            "gross_monthly", "epf_wage_base", "epf_employer", "esi_employer",
            "epf_employee", "esi_employee", "net_monthly", "gross_without_esi",
            "annual_gross", "annual_net",
            "monthly_ctc_reconciliation_error",
        )
        flag_keys = (
            "epf_ceiling_applied", "esi_applicable", "esi_employee_exempt",
            "esi_rule_ambiguous",
        )
        for key in money_keys:
            with self.subTest(key=key):
                self.assertIsInstance(payload[key], str)
                Decimal(payload[key])                     # must round-trip
        for key in flag_keys:
            with self.subTest(key=key):
                self.assertIsInstance(payload[key], bool)
        for key, value in payload.items():
            with self.subTest(key=key):
                self.assertNotIsInstance(value, float)


# ==========================================================================
# 4. Input handling
# ==========================================================================

class TestInputHandling(unittest.TestCase):

    def test_accepts_int_float_str_and_decimal_identically(self):
        reference = calculate_breakdown(D("180000"))
        for value in (180000, 180000.0, "180000", "180,000", "180000.00", "18_0000"):
            with self.subTest(value=value):
                self.assertEqual(calculate_breakdown(value).net_monthly,
                                 reference.net_monthly)

    def test_float_input_is_not_subject_to_binary_error(self):
        """A float that is not exactly representable must still round cleanly."""
        b = calculate_breakdown(0.1 + 0.2)              # 0.30000000000000004
        self.assertEqual(b.monthly_ctc, D("0.03"))       # 0.025 -> 0.03 half-up
        self.assertEqual(b.ctc_reconciliation_error(), D("0.00"))

    def test_paise_level_ctc_is_handled(self):
        """Tiny-but-representable CTCs still produce a reconciling structure."""
        for ctc in ("0.36", "1.00", "12.00", "99.99"):
            with self.subTest(ctc=ctc):
                b = calculate_breakdown(D(ctc))
                self.assertEqual(b.ctc_reconciliation_error(), D("0.00"))
                self.assertGreaterEqual(b.net_monthly, D("0.00"))

    def test_every_ctc_from_1_to_10_reconciles(self):
        c = D("1.00")
        while c <= D("10.00"):
            with self.subTest(ctc=c):
                self.assertEqual(
                    calculate_breakdown(c).ctc_reconciliation_error(), D("0.00")
                )
            c += D("0.01")

    def test_ctc_too_small_for_the_structure_raises(self):
        """
        The degenerate band (annual CTC 0.06 - 0.17) is where rounding pushes
        Basic above the entire monthly CTC, leaving no room for HRA. The
        calculator must reject it loudly rather than emit negative pay.
        """
        for bad in ("0.06", "0.10", "0.12", "0.17"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    calculate_breakdown(D(bad))
                self.assertIn("special allowance", str(ctx.exception))

    def test_rejects_non_positive_ctc(self):
        for bad in (0, -1, -180000, "0", "-5000.50"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    calculate_breakdown(bad)

    def test_rejects_garbage_ctc(self):
        for bad in ("abc", "", None, "12,00,0000x", float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    calculate_breakdown(bad)

    def test_rejects_invalid_structure_parameters(self):
        with self.assertRaises(ValueError):
            calculate_breakdown(D("1200000"), basic_pct=D("150"))
        with self.assertRaises(ValueError):
            calculate_breakdown(D("1200000"), basic_pct=D("-10"))
        with self.assertRaises(ValueError):
            calculate_breakdown(D("1200000"), hra_pct_of_basic=D("500"))
        with self.assertRaises(ValueError):
            calculate_breakdown(D("1200000"), days_per_month=0)

    def test_basic_and_hra_can_be_rebalanced(self):
        """Lowering basic lowers the PF base - the main cost lever employers use."""
        b = calculate_breakdown(D("1200000"), basic_pct=D("40"), hra_pct_of_basic=D("50"))
        self.assertEqual(b.basic, D("40000.00"))
        self.assertEqual(b.hra, D("20000.00"))
        self.assertEqual(b.epf_employer, D("3000.00"))   # still capped
        self.assertEqual(b.ctc_reconciliation_error(), D("0.00"))

        c = calculate_breakdown(D("600000"), basic_pct=D("40"), hra_pct_of_basic=D("50"))
        self.assertEqual(c.basic, D("20000.00"))
        self.assertEqual(c.epf_employer, D("2400.00"))   # 12% x 20,000, below ceiling

    def test_impossible_structure_raises_clear_error(self):
        with self.assertRaises(ValueError) as ctx:
            calculate_breakdown(D("50000"), basic_pct=D("100"), hra_pct_of_basic=D("50"))
        self.assertIn("special allowance", str(ctx.exception))


# ==========================================================================
# 5. Output / interface
# ==========================================================================

class TestOutput(unittest.TestCase):

    def test_to_dict_is_json_serialisable(self):
        b = calculate_breakdown(D("180000"))
        payload = json.dumps(b.to_dict())
        self.assertIn("net_monthly", payload)
        self.assertEqual(json.loads(payload)["net_monthly"], "12653.75")

    def test_report_contains_every_required_line_item(self):
        text = format_breakdown(calculate_breakdown(D("180000")))
        for label in (
            "Basic Salary", "House Rent Allowance", "Special Allowance",
            "Employer EPF", "Employee EPF", "Employer ESI", "Employee ESI",
            "GROSS MONTHLY SALARY", "NET MONTHLY SALARY",
        ):
            with self.subTest(label=label):
                self.assertIn(label, text)

    def test_report_discloses_what_is_missing(self):
        """The report must not present net as a real bank credit."""
        text = format_breakdown(calculate_breakdown(D("180000")))
        self.assertIn("NOT INCLUDED", text)
        self.assertIn("TDS", text)
        self.assertIn("Professional tax", text)

    def test_report_shows_pf_ceiling_when_capped(self):
        self.assertIn("ceiling", format_breakdown(calculate_breakdown(D("3500000"))))

    def test_report_flags_esi_exemption(self):
        self.assertIn("EXEMPT", format_breakdown(calculate_breakdown(D("69000"))))

    def test_dataclass_is_immutable(self):
        b = calculate_breakdown(D("180000"))
        with self.assertRaises(Exception):
            b.net_monthly = D("1.00")  # type: ignore[misc]

    def test_result_type(self):
        self.assertIsInstance(calculate_breakdown(D("180000")), SalaryBreakdown)


class TestCLI(unittest.TestCase):

    def test_cli_json_output(self):
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = main(["180000", "--json"])
        self.assertEqual(exit_code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["basic"], "7500.00")
        self.assertEqual(data["net_monthly"], "12653.75")

    def test_cli_table_output(self):
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = main(["1200000"])
        self.assertEqual(exit_code, 0)
        self.assertIn("NET MONTHLY SALARY", buf.getvalue())

    def test_cli_returns_error_code_for_bad_input(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            exit_code = main(["-5000"])
        self.assertEqual(exit_code, 2)
        self.assertIn("error", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
