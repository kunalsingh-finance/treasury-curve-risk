"""Analytic and numerical oracles for curve-implied abstract bond mathematics."""

import math
import unittest
from dataclasses import FrozenInstanceError

from treasury_risk import (
    KEY_RATE_NODES, BumpedCurve, CouponBond, CurveMathError, SvenssonCurve,
    convexity, effective_duration, key_rate_dv01, node_bumped_curve,
    parallel_bumped_curve, parallel_dv01, price,
)


def flat(rate_percent=5.0):
    return SvenssonCurve(rate_percent, 0, 0, 0, 1, 2)


class SvenssonTests(unittest.TestCase):
    def test_flat_curve_yields_and_discount_have_analytic_values(self):
        curve = flat()
        for time in (0, 1e-12, .5, 2, 30):
            with self.subTest(time=time):
                self.assertEqual(curve.zero_yield(time), .05)
                self.assertEqual(curve.forward_yield(time), .05)
                self.assertAlmostEqual(curve.discount(time), math.exp(-.05 * time), places=15)

    def test_percent_scaling_and_analytic_time_zero(self):
        curve = SvenssonCurve(4, -2, 1.5, .7, 1.2, 3.4)
        self.assertEqual(curve.zero_yield(0), .02)
        self.assertEqual(curve.forward_yield(0), .02)
        self.assertEqual(curve.discount(0), 1)
        with self.assertRaises(FrozenInstanceError):
            curve.beta0 = 5

    def test_tiny_curvature_avoids_subtracting_two_nearly_equal_ones(self):
        curve = SvenssonCurve(0, 0, 100, 0, 1, 2)
        time = 1e-14
        expected = time / 2 - time**2 / 3
        self.assertTrue(math.isclose(curve.zero_yield(time), expected, rel_tol=1e-14, abs_tol=0))
        self.assertTrue(math.isclose(curve.forward_yield(time), time * math.exp(-time), rel_tol=1e-14))

    def test_forward_matches_derivative_of_time_times_zero_yield(self):
        curve = SvenssonCurve(4, -2, 1.5, .7, 1.2, 3.4)
        for time in (.001, .1, .5, 2, 10, 30):
            step = min(1e-5, time / 10)
            numerical = ((time + step) * curve.zero_yield(time + step) - (time - step) * curve.zero_yield(time - step)) / (2 * step)
            with self.subTest(time=time):
                self.assertAlmostEqual(numerical, curve.forward_yield(time), delta=2e-10)

    def test_nonflat_formula_matches_a_direct_well_conditioned_example(self):
        curve = SvenssonCurve(4, -2, 1.5, .7, 1.2, 3.4)
        time = 5
        x, z = time / 1.2, time / 3.4
        expected = (4 - 2 * (1 - math.exp(-x)) / x + 1.5 * ((1 - math.exp(-x)) / x - math.exp(-x)) + .7 * ((1 - math.exp(-z)) / z - math.exp(-z))) / 100
        self.assertAlmostEqual(curve.zero_yield(time), expected, places=15)

    def test_negative_yields_are_supported_without_a_floor(self):
        curve = flat(-2)
        self.assertEqual(curve.zero_yield(5), -.02)
        self.assertAlmostEqual(curve.discount(5), math.exp(.1), places=15)
        self.assertGreater(curve.discount(5), 1)

    def test_extreme_tau_loading_has_a_finite_asymptote(self):
        curve = SvenssonCurve(3, -1, 2, 1, 1e-300, 1e-300)
        self.assertEqual(curve.zero_yield(1e300), .03)
        self.assertEqual(curve.forward_yield(1e300), .03)
        self.assertEqual(curve.zero_yield(0), .02)
        self.assertEqual(curve.forward_yield(0), .02)

    def test_invalid_parameters_time_and_unrepresentable_discounts_reject(self):
        for tau in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(tau=tau), self.assertRaises(CurveMathError):
                SvenssonCurve(4, 0, 0, 0, tau, 2)
        for beta in (float("nan"), float("inf"), "4", True):
            with self.subTest(beta=beta), self.assertRaises(CurveMathError):
                SvenssonCurve(beta, 0, 0, 0, 1, 2)
        for time in (-1, float("nan"), float("inf"), True, "1"):
            with self.subTest(time=time), self.assertRaises(CurveMathError):
                flat().zero_yield(time)
        with self.assertRaises(CurveMathError):
            flat(-10000).discount(30)
        with self.assertRaises(CurveMathError):
            flat(10000).discount(30)


class BumpTests(unittest.TestCase):
    def test_single_node_shape_and_endpoint_flat_tails(self):
        base = flat()
        middle = node_bumped_curve(base, 2, 10)
        for time, fraction in ((0, 0), (1, 0), (1.5, .5), (2, 1), (2.5, .5), (3, 0), (40, 0)):
            with self.subTest(time=time):
                self.assertAlmostEqual(middle.zero_yield(time) - base.zero_yield(time), fraction * .001, places=15)
        first = node_bumped_curve(base, .5, 1)
        last = node_bumped_curve(base, 30, 1)
        self.assertAlmostEqual(first.zero_yield(0) - base.zero_yield(0), .0001, places=15)
        self.assertAlmostEqual(last.zero_yield(100) - base.zero_yield(100), .0001, places=15)

    def test_nodal_hat_functions_partition_parallel_bump_including_tails(self):
        base = SvenssonCurve(4, -2, 1.5, .7, 1.2, 3.4)
        all_nodes = BumpedCurve(base, key_rate_bumps_bps={node: 1 for node in KEY_RATE_NODES})
        parallel = parallel_bumped_curve(base, 1)
        for time in (0, .01, .5, .7, 1.5, 4, 6, 8, 15, 25, 30, 50):
            with self.subTest(time=time):
                self.assertAlmostEqual(all_nodes.zero_yield(time), parallel.zero_yield(time), places=15)
                self.assertAlmostEqual(all_nodes.discount(time), parallel.discount(time), places=15)

    def test_parallel_and_nodal_bumps_combine_and_snapshot_the_input_mapping(self):
        source = {2: 10}
        curve = BumpedCurve(flat(), parallel_bps=-2, key_rate_bumps_bps=source)
        source[2] = 100
        self.assertAlmostEqual(curve.zero_yield(2), .0508, places=15)
        with self.assertRaises(TypeError):
            curve.key_rate_bumps_bps[2] = 0

    def test_invalid_nodes_and_bumps_reject(self):
        for nodes in ((), (2, 1), (1, 1), (0, 1), (1, float("inf"))):
            with self.subTest(nodes=nodes), self.assertRaises(CurveMathError):
                BumpedCurve(flat(), nodes=nodes)
        for node, shock in ((4, 1), (2, float("nan")), (True, 1)):
            with self.subTest(node=node, shock=shock), self.assertRaises(CurveMathError):
                node_bumped_curve(flat(), node, shock)
        with self.assertRaises(CurveMathError):
            node_bumped_curve(flat(), 2, 1, nodes=None)


class BondTests(unittest.TestCase):
    def test_regular_semiannual_cash_flows_have_no_stub_or_time_zero_flow(self):
        bond = CouponBond(2.5, .05)
        self.assertEqual(bond.cashflows(), ((.5, 2.5), (1, 2.5), (1.5, 2.5), (2, 2.5), (2.5, 102.5)))
        self.assertEqual(math.fsum(amount for _, amount in bond.cashflows()), 112.5)
        self.assertEqual(CouponBond(2, .06, face=200, frequency=1).cashflows(), ((1, 12), (2, 212)))

    def test_zero_coupon_pv_and_finite_difference_risk_have_analytic_values(self):
        bond = CouponBond(5, 0)
        curve = flat()
        expected_price = 100 * math.exp(-.05 * 5)
        self.assertAlmostEqual(price(bond, curve), expected_price, places=13)
        self.assertAlmostEqual(parallel_dv01(bond, curve), expected_price * math.sinh(5e-4), places=13)
        self.assertAlmostEqual(effective_duration(bond, curve), math.sinh(5e-4) / 1e-4, places=10)
        self.assertAlmostEqual(effective_duration(bond, curve), 5, delta=3e-7)
        self.assertAlmostEqual(convexity(bond, curve), 25, delta=1e-6)

    def test_coupon_pv_matches_geometric_series_on_flat_curve(self):
        bond = CouponBond(10, .06)
        discount_per_period = math.exp(-.05 / 2)
        expected = 3 * discount_per_period * (1 - discount_per_period**20) / (1 - discount_per_period) + 100 * discount_per_period**20
        self.assertAlmostEqual(bond.price(flat()), expected, places=11)

    def test_parallel_dv01_matches_sum_of_key_rate_buckets_with_nonlinear_tolerance(self):
        bond = CouponBond(30, .045)
        curve = SvenssonCurve(4, -2, 1.5, .7, 1.2, 3.4)
        buckets = bond.key_rate_dv01(curve)
        parallel = bond.parallel_dv01(curve)
        self.assertEqual(tuple(buckets), KEY_RATE_NODES)
        self.assertTrue(all(value >= 0 for value in buckets.values()))
        self.assertTrue(math.isclose(math.fsum(buckets.values()), parallel, rel_tol=2e-6, abs_tol=1e-10))
        self.assertGreater(parallel, 0)

    def test_zero_coupon_maturity_at_a_node_has_only_its_matching_key_rate(self):
        bond = CouponBond(5, 0)
        buckets = key_rate_dv01(bond, flat())
        self.assertAlmostEqual(buckets[5], parallel_dv01(bond, flat()), places=15)
        self.assertTrue(all(value == 0 for node, value in buckets.items() if node != 5))

    def test_negative_yield_zero_coupon_price_is_above_face_and_duration_positive(self):
        bond = CouponBond(5, 0)
        self.assertAlmostEqual(bond.price(flat(-2)), 100 * math.exp(.1), places=12)
        self.assertGreater(bond.price(flat(-2)), bond.face)
        self.assertGreater(bond.effective_duration(flat(-2)), 0)

    def test_price_and_dollar_sensitivities_scale_with_face(self):
        unit, double = CouponBond(10, .05), CouponBond(10, .05, face=200)
        self.assertAlmostEqual(double.price(flat()), 2 * unit.price(flat()), places=12)
        self.assertAlmostEqual(double.parallel_dv01(flat()), 2 * unit.parallel_dv01(flat()), places=12)
        self.assertAlmostEqual(double.effective_duration(flat()), unit.effective_duration(flat()), places=12)
        self.assertAlmostEqual(double.convexity(flat()), unit.convexity(flat()), places=12)

    def test_representable_large_flows_do_not_fail_from_avoidable_intermediate_overflow(self):
        # Dividing the annual rate before multiplying face is necessary here:
        # face*rate overflows, while the half-year coupon plus principal is finite.
        bond = CouponBond(.5, 8, face=3e307)
        self.assertTrue(math.isclose(bond.cashflows()[-1][1], 1.5e308, rel_tol=1e-15))
        self.assertTrue(math.isclose(bond.price(flat(0)), 1.5e308, rel_tol=1e-15))
        self.assertAlmostEqual(bond.effective_duration(flat(0)), .5, delta=1e-8)
        with self.assertRaises(CurveMathError):
            CouponBond(.5, 8, face=1e308)

    def test_unsupported_stubs_frequencies_and_nonfinite_bond_values_reject(self):
        invalid = [
            {"maturity_years": 0}, {"maturity_years": -1}, {"maturity_years": 2.7},
            {"maturity_years": 201}, {"maturity_years": float("nan")},
            {"coupon_rate": -.01}, {"coupon_rate": float("inf")},
            {"face": 0}, {"face": -100}, {"face": True}, {"frequency": 4}, {"frequency": True},
            {"frequency": 1, "maturity_years": 2.5},
        ]
        for change in invalid:
            parameters = {"maturity_years": 5, "coupon_rate": .05, **change}
            with self.subTest(change=change), self.assertRaises(CurveMathError):
                CouponBond(**parameters)

    def test_nonpositive_and_nonfinite_custom_discount_results_reject(self):
        class BadCurve:
            def __init__(self, value):
                self.value = value

            def discount(self, time):
                return self.value

        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(CurveMathError):
                CouponBond(5, .05).price(BadCurve(value))


if __name__ == "__main__":
    unittest.main()
