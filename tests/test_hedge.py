"""Hand-calculated hedge examples and risk/shape controls."""

import json
import math
import unittest

import numpy as np

from treasury_risk.hedge import KEY_RATE_NODES, parallel_hedge, select_hedge


def bucket(*values):
    return np.array(list(values) + [0.0] * (len(KEY_RATE_NODES) - len(values)))


class HedgeTests(unittest.TestCase):
    def test_full_rank_diagonal_hedge_has_independent_exact_solution(self):
        matrix = np.diag(np.arange(1.0, 10.0))
        wanted_weights = np.array([-2.0, 3.0, -4.0, 5.0, -6.0, 7.0, -8.0, 9.0, -10.0])
        target = np.array([2.0, -6.0, 12.0, -20.0, 30.0, -42.0, 56.0, -72.0, 90.0])
        result = select_hedge(target, matrix)
        np.testing.assert_allclose(result["weights"], wanted_weights, rtol=0, atol=1e-12)
        np.testing.assert_allclose(result["residual_exposure"], np.zeros(9), rtol=0, atol=1e-12)
        self.assertEqual(result["matrix_rank"], 9)
        self.assertTrue(result["fully_spans_key_rates"])
        self.assertTrue(result["unique_weights"])
        self.assertFalse(result["rank_deficient"])
        self.assertAlmostEqual(result["condition_number"], 9.0)
        self.assertAlmostEqual(result["gross_absolute_face_units"], 5400.0)
        self.assertEqual(result["face_amounts"][0], -200.0)

    def test_rank_deficient_solution_leaves_unspanned_risk_and_is_minimum_norm(self):
        # Solve 2*w1+4*w2=-6: minimum-norm solution is (-.6,-1.2).
        matrix = np.column_stack((bucket(2), bucket(4)))
        result = select_hedge(bucket(6, 8), matrix)
        np.testing.assert_allclose(result["weights"], [-0.6, -1.2], atol=1e-12)
        np.testing.assert_allclose(result["residual_exposure"], bucket(0, 8), atol=1e-12)
        self.assertAlmostEqual(result["target_norm"], 10.0)
        self.assertAlmostEqual(result["residual_norm"], 8.0)
        self.assertAlmostEqual(result["residual_ratio"], 0.8)
        self.assertEqual(result["matrix_rank"], 1)
        self.assertTrue(result["rank_deficient"])
        self.assertFalse(result["unique_weights"])
        self.assertFalse(result["fully_spans_key_rates"])
        self.assertTrue(result["ill_conditioned"])
        json.dumps(result, allow_nan=False)

    def test_exact_portfolio_match_does_not_claim_every_curve_bucket_is_spanned(self):
        result = select_hedge(bucket(6), bucket(2)[:, None])
        self.assertAlmostEqual(result["weights"][0], -3.0)
        self.assertAlmostEqual(result["residual_norm"], 0.0)
        self.assertFalse(result["fully_spans_key_rates"])
        self.assertEqual(result["matrix_rank"], 1)

    def test_more_instruments_than_nodes_gives_minimum_norm_nonunique_weights(self):
        # Two identical complete hedge sets: equal half-unit shorts minimize norm.
        matrix = np.column_stack((np.eye(9), np.eye(9)))
        result = select_hedge(np.ones(9), matrix)
        np.testing.assert_allclose(result["weights"], np.full(18, -0.5), atol=1e-12)
        self.assertLess(result["residual_norm"], 1e-12)
        self.assertEqual(result["matrix_rank"], 9)
        self.assertTrue(result["fully_spans_key_rates"])
        self.assertFalse(result["unique_weights"])
        self.assertFalse(result["rank_deficient"])
        self.assertAlmostEqual(result["gross_absolute_face_units"], 900.0)

    def test_ill_conditioned_but_full_column_rank_matrix_has_diagnostics(self):
        matrix = np.column_stack((bucket(1), bucket(1, 1e-12)))
        result = select_hedge(bucket(1, 1e-12), matrix)
        self.assertEqual(result["matrix_rank"], 2)
        self.assertFalse(result["rank_deficient"])
        self.assertGreater(result["condition_number"], 1e11)
        self.assertTrue(result["ill_conditioned"])
        self.assertLess(result["residual_norm"], 1e-12)
        self.assertEqual(len(result["singular_values"]), 2)

    def test_ridge_penalty_matches_hand_calculated_tradeoff(self):
        # Minimize (6+2*w)^2+5*w^2, giving w=-12/9 and residual10/3.
        result = select_hedge(bucket(6), bucket(2)[:, None], ridge_penalty=5)
        self.assertAlmostEqual(result["weights"][0], -4 / 3)
        self.assertAlmostEqual(result["residual_exposure"][0], 10 / 3)
        self.assertEqual(result["ridge_penalty"], 5.0)
        self.assertEqual(result["matrix_rank"], 1)
        self.assertFalse(result["fully_spans_key_rates"])

    def test_ridge_does_not_hide_original_rank_deficiency(self):
        matrix = np.column_stack((bucket(2), bucket(4)))
        result = select_hedge(bucket(6, 8), matrix, ridge_penalty=1)
        np.testing.assert_allclose(result["weights"], [-12 / 21, -24 / 21], atol=1e-12)
        self.assertTrue(result["rank_deficient"])
        self.assertFalse(result["unique_weights"])
        self.assertGreater(result["residual_norm"], 8)

    def test_target_and_instrument_scaling_preserve_sign_and_units(self):
        matrix = np.column_stack((bucket(2), bucket(0, 4)))
        original = select_hedge(bucket(6, -8), matrix)
        scaled_target = select_hedge(bucket(-12, 16), matrix)
        scaled_matrix = select_hedge(bucket(6, -8), -2 * matrix)
        np.testing.assert_allclose(original["weights"], [-3, 2], atol=1e-12)
        np.testing.assert_allclose(scaled_target["weights"], [6, -4], atol=1e-12)
        np.testing.assert_allclose(scaled_matrix["weights"], [1.5, -1], atol=1e-12)
        self.assertAlmostEqual(original["gross_absolute_face_units"], 500)
        self.assertAlmostEqual(scaled_matrix["gross_absolute_face_units"], 250)

    def test_parallel_hedge_matches_total_but_can_increase_bucket_risk(self):
        result = parallel_hedge(bucket(10, -3), bucket(0, 2))
        self.assertAlmostEqual(result["weights"][0], -3.5)
        self.assertAlmostEqual(result["target_total_dv01"], 7.0)
        self.assertAlmostEqual(result["residual_total_dv01"], 0.0)
        np.testing.assert_allclose(result["residual_exposure"], bucket(10, -10), atol=1e-12)
        self.assertAlmostEqual(result["residual_norm"], math.sqrt(200))
        self.assertGreater(result["residual_norm"], result["target_norm"])
        self.assertFalse(result["fully_spans_key_rates"])

    def test_zero_target_needs_no_trade(self):
        result = select_hedge(np.zeros(9), bucket(2)[:, None])
        self.assertEqual(result["weights"], [0.0])
        self.assertEqual(result["gross_absolute_face_units"], 0.0)
        self.assertIsNone(result["residual_ratio"])
        self.assertEqual(parallel_hedge(np.zeros(9), bucket(2))["weights"], [-0.0])
        json.dumps(result, allow_nan=False)

    def test_invalid_shapes_nonfinite_and_nonnumeric_inputs_reject(self):
        target, matrix = bucket(6), bucket(2)[:, None]
        cases = [
            (target[:8], matrix), (target[:, None], matrix), (target, matrix[:8]),
            (target, bucket(2)), (target, np.empty((9, 0))), (target, np.zeros((9, 1))),
            (target, np.column_stack((bucket(2), np.zeros(9)))),
            (bucket(float("nan")), matrix), (bucket(float("inf")), matrix),
            (target, bucket(float("-inf"))[:, None]), (["1"] * 9, matrix),
            ([True] * 9, matrix), (np.ones(9, dtype=complex), matrix),
        ]
        for target_value, matrix_value in cases:
            with self.subTest(target=target_value, matrix=matrix_value), self.assertRaises(ValueError):
                select_hedge(target_value, matrix_value)
        for invalid in (-1, float("nan"), float("inf"), True, "1"):
            with self.subTest(ridge=invalid), self.assertRaises(ValueError):
                select_hedge(target, matrix, ridge_penalty=invalid)

    def test_parallel_hedge_rejects_zero_total_and_invalid_vectors(self):
        for invalid in (np.zeros(9), bucket(1, -1), bucket(float("nan")), np.ones(8), np.ones((9, 1))):
            with self.subTest(hedge=invalid), self.assertRaises(ValueError):
                parallel_hedge(bucket(6), invalid)

    def test_inputs_are_not_mutated_and_outputs_are_json_finite(self):
        target = bucket(6, 8)
        matrix = np.column_stack((bucket(2), bucket(4)))
        before_target, before_matrix = target.copy(), matrix.copy()
        json.dumps(select_hedge(target, matrix, ridge_penalty=1), allow_nan=False)
        np.testing.assert_array_equal(target, before_target)
        np.testing.assert_array_equal(matrix, before_matrix)

    def test_unreportable_derived_amounts_fail_with_controlled_error(self):
        with self.assertRaises(ValueError):
            parallel_hedge(bucket(1e308), bucket(1e-308))


if __name__ == "__main__":
    unittest.main()
