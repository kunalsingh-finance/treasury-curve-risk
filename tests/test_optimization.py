"""Independent constrained hedge and chronological covariance oracles."""

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from treasury_risk.optimization import estimate_covariance, optimize_covariance_hedge


def vector(*values):
    return np.array(list(values) + [0.0] * (9 - len(values)))


def example(**changes):
    parameters = {"target_exposure": vector(2, 4),
                  "hedge_matrix": np.column_stack((vector(1), vector(0, 1))),
                  "covariance": np.diag([1, 9] + [0] * 7),
                  "target_parallel_dv01": 6, "hedge_parallel_dv01": [1, 2],
                  "hedge_prices": [100, 100], "gross_face_limit": 600,
                  "position_face_limits": [200, 400]}
    parameters.update(changes)
    return optimize_covariance_hedge(**parameters)


class OptimizationTests(unittest.TestCase):
    def test_independent_equality_constrained_variance_solution(self):
        # Residual x+2y=4; minimizing x²+9y² gives x36/13,y8/13.
        result = example()
        self.assertEqual(result["status"], "optimal")
        np.testing.assert_allclose(result["weights"], [10 / 13, -44 / 13], atol=1e-5)
        np.testing.assert_allclose(result["residual_exposure"], vector(36 / 13, 8 / 13), atol=1e-5)
        self.assertAlmostEqual(result["variance_before"], 148)
        self.assertAlmostEqual(result["variance_after"], 144 / 13, places=6)
        self.assertLess(abs(result["parallel_residual_dv01"]), 1e-8)
        self.assertLessEqual(result["solver_diagnostics"]["normalized_first_order_gap"], 1e-7)
        json.dumps(result, allow_nan=False)

    def test_per_position_and_gross_bounds_are_real_constraints(self):
        result = example(position_face_limits=[100, 250], gross_face_limit=350)
        self.assertEqual(result["status"], "optimal")
        np.testing.assert_allclose(result["weights"], [-1, -2.5], atol=1e-7)
        self.assertAlmostEqual(result["gross_absolute_face_units"], 350)
        self.assertAlmostEqual(result["variance_after"], 21.25)
        self.assertTrue(all(value >= 0 for value in result["position_face_capacity_remaining"]))

    def test_unique_boundary_solution_is_certified_without_slsqp_roundoff(self):
        # The equality needs all 100 and 250 face capacity, so only (-1,-2.5)
        # is feasible. A Linux SLSQP roundoff failure cannot improve that point.
        roundoff = SimpleNamespace(success=False, status=8, message="Positive directional derivative for linesearch",
                                   x=np.array([-1 / 3.5, -2.5 / 3.5, 1 / 3.5, 2.5 / 3.5]), nit=1)
        with patch("treasury_risk.optimization.minimize", return_value=roundoff) as nonlinear:
            result = example(position_face_limits=[100, 250], gross_face_limit=350)
        nonlinear.assert_not_called()
        self.assertEqual(result["status"], "optimal")
        np.testing.assert_allclose(result["weights"], [-1, -2.5], atol=1e-7)
        self.assertAlmostEqual(result["variance_after"], 21.25)
        self.assertEqual(result["solver_diagnostics"]["optimization"]["iterations"], 0)
        self.assertTrue(result["solver_diagnostics"]["optimality_lp"]["success"])
        self.assertLessEqual(result["solver_diagnostics"]["normalized_first_order_gap"], 1e-7)
        self.assertLessEqual(abs(result["parallel_residual_dv01"]),
                             result["solver_diagnostics"]["parallel_tolerance"])

    def test_infeasible_gross_or_position_capacity_withholds_positions(self):
        for parameters in ({"gross_face_limit": 299}, {"position_face_limits": [0, 299]}):
            with self.subTest(parameters=parameters):
                result = example(**parameters)
                self.assertEqual(result["status"], "infeasible")
                self.assertIsNone(result["weights"])
                self.assertIsNone(result["face_amounts"])
                self.assertIsNone(result["variance_after"])

    def test_cash_neutrality_has_explicit_equality_and_can_be_infeasible(self):
        result = example(cash_neutral=True, gross_face_limit=1200, position_face_limits=[600, 600])
        self.assertEqual(result["status"], "optimal")
        np.testing.assert_allclose(result["weights"], [6, -6], atol=1e-7)
        self.assertAlmostEqual(result["hedge_cash_dollars"], 0, places=6)
        self.assertAlmostEqual(result["parallel_residual_dv01"], 0, places=8)
        impossible = example(cash_neutral=True, hedge_prices=[100, 200], gross_face_limit=10000, position_face_limits=[5000, 5000])
        self.assertEqual(impossible["status"], "infeasible")
        self.assertIsNone(impossible["weights"])

    def test_ridge_tradeoff_matches_separate_hand_solution(self):
        # Parallel equality fixes residuals y=-x; objective14x²+8x+40.
        result = example(hedge_parallel_dv01=[1, 1], ridge_penalty=2, position_face_limits=[400, 400])
        self.assertEqual(result["status"], "optimal")
        np.testing.assert_allclose(result["weights"], [-16 / 7, -26 / 7], atol=1e-5)

    def test_solver_failure_and_fake_success_never_release_positions(self):
        failed = SimpleNamespace(success=False, status=9, message="iteration limit", x=np.zeros(4), nit=1000)
        with patch("treasury_risk.optimization.minimize", return_value=failed):
            result = example()
        self.assertEqual(result["status"], "solver_failed")
        self.assertIsNone(result["weights"])
        # This feasible but suboptimal vertex is not certified just by success=True.
        fake = SimpleNamespace(success=True, status=0, message="fake success", x=np.array([0, -.5, 0, .5]), nit=1)
        with patch("treasury_risk.optimization.minimize", return_value=fake):
            result = example()
        self.assertEqual(result["status"], "solver_failed")
        self.assertIsNone(result["weights"])
        self.assertGreater(result["solver_diagnostics"]["normalized_first_order_gap"], 1e-7)

    def test_zero_capacity_with_zero_obligation_is_valid_fixed_solution(self):
        result = example(target_parallel_dv01=0, gross_face_limit=0, position_face_limits=[0, 0])
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["weights"], [0, 0])
        self.assertAlmostEqual(result["variance_after"], 148)

    def test_portfolio_scale_convergence_matches_closed_form_interior_optimum(self):
        # Offline regression for July-2022-sized risks. Limits do not bind, so
        # equality-constrained quadratic algebra supplies an independent oracle.
        target = np.array([3.7714667296029347,181.477725321173,108.37249100120516,554.8753379446225,1098.4296096387425,1307.234364973624,2981.201944617027,0,0])
        matrix = np.array([
            [1.409702841925764e-5,3.5242571051696814e-5,4.0642279103053625e-5,5.911604232267109e-5,5.5421289680168684e-5],
            [.007966239190324131,.00010511542619440206,.00011547201201977941,.00016795929020929634,.00015746183456855078],
            [.0056716229183848554,.0002332056530178761,.00025813557245868424,.00037546992357562203,.0003520030533508134],
            [0,.011799829321226696,.0006188178201611549,.0009000986475058426,.0008438424820411683],
            [0,.027789053128671526,.0011798052342157916,.0017160803406852665,.0016088253193871083],
            [0,0,.016362179061310655,.0029180144805351915,.002735638575501298],
            [0,0,.05695268544439358,.01694744359670608,.010006458828776488],
            [0,0,0,.1054964966602796,.025712638169643753],
            [0,0,0,0,.11254615154152958]])
        covariance = np.array([
            [16.459638416429055,10.965647614910392,11.452155409355742,11.988668799430375,11.344997243350099,9.847630437952754,7.952561475225356,5.72690639972426,5.555019170633951],
            [10.965647614910392,22.930230803422273,23.964518682949084,23.77297008239339,22.228726824757178,20.622861861069126,17.944495602525553,11.919955054254933,10.614385345631165],
            [11.452155409355742,23.964518682949084,36.258027868854626,34.09669852031551,32.25345830874287,29.771035392663116,25.97454875085889,17.87844747791942,15.763525698054293],
            [11.988668799430375,23.77297008239339,34.09669852031551,39.75742187329336,35.534568481026135,33.1245809368406,29.24800104357111,21.09929491481482,18.799089212841345],
            [11.344997243350099,22.228726824757178,32.25345830874287,35.534568481026135,40.37240235399216,36.216385056546784,33.25942441461088,25.55952491221903,23.65785904423515],
            [9.847630437952754,20.622861861069126,29.771035392663116,33.1245809368406,36.216385056546784,40.10124019304664,35.01459725752057,28.091651692315867,26.438780571264065],
            [7.952561475225356,17.944495602525553,25.97454875085889,29.24800104357111,33.25942441461088,35.01459725752057,37.91098005806518,29.373206351108983,27.78680584044847],
            [5.72690639972426,11.919955054254933,17.87844747791942,21.09929491481482,25.55952491221903,28.091651692315867,29.373206351108983,31.52135953082011,27.480410994276134],
            [5.555019170633951,10.614385345631165,15.763525698054293,18.799089212841345,23.65785904423515,26.438780571264065,27.78680584044847,27.480410994276134,31.4114277525286]])
        parallel = np.array([.013651959170339012,.03996244689214734,.07552774267499274,.12858069397462657,.15401848237122095])
        target_parallel = 6235.363338860935
        quadratic = matrix.T @ covariance @ matrix
        unconstrained = np.linalg.solve(quadratic, -(matrix.T @ covariance @ target))
        direction = np.linalg.solve(quadratic, parallel)
        oracle = unconstrained - direction * ((parallel @ unconstrained + target_parallel) / (parallel @ direction))
        self.assertLess(sum(abs(100 * oracle)), 24_000_000)
        self.assertTrue(np.all(abs(100 * oracle) < 18_000_000))
        result = optimize_covariance_hedge(target,matrix,covariance,target_parallel_dv01=target_parallel,
            hedge_parallel_dv01=parallel,hedge_prices=[100] * 5,gross_face_limit=24_000_000,
            position_face_limits=[18_000_000] * 5)
        self.assertEqual(result["status"], "optimal")
        np.testing.assert_allclose(result["weights"], oracle, atol=.01)
        residual = target + matrix @ oracle
        self.assertAlmostEqual(result["variance_after"], residual @ covariance @ residual, delta=1e-5)
        self.assertLess(result["solver_diagnostics"]["normalized_first_order_gap"], 1e-7)
        self.assertLess(abs(result["parallel_residual_dv01"]), result["solver_diagnostics"]["parallel_tolerance"])

    def test_invalid_covariance_shapes_and_nonfinite_inputs_reject(self):
        asymmetric = np.eye(9); asymmetric[0, 1] = .1
        invalid = [{"covariance": asymmetric}, {"covariance": np.diag([-1] + [1] * 8)},
                   {"covariance": np.ones((8, 8))}, {"covariance": np.full((9, 9), np.nan)},
                   {"target_exposure": vector(float("inf"))}, {"hedge_parallel_dv01": [1]},
                   {"hedge_prices": [100, 0]}, {"gross_face_limit": -1}, {"gross_face_limit": float("inf")},
                   {"position_face_limits": [100, -1]}, {"ridge_penalty": -1}, {"cash_neutral": "False"}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                example(**change)


def history():
    return [{"date": "2020-01-01", "zero_yields_bps": vector(0, 0).tolist()},
            {"date": "2020-01-02", "zero_yields_bps": vector(1, 2).tolist()},
            {"date": "2020-01-03", "zero_yields_bps": vector(3, 6).tolist()}]


class CovarianceTests(unittest.TestCase):
    def test_sample_covariance_and_pca_have_hand_calculated_rank_one_oracle(self):
        result = estimate_covariance(history(), "2020-01-04", lookback_changes=2, shrinkage=0)
        expected = np.zeros((9, 9)); expected[:2, :2] = [[.5, 1], [1, 2]]
        np.testing.assert_allclose(result["covariance"], expected, atol=1e-12)
        self.assertAlmostEqual(result["pca"]["eigenvalues"][0], 2.5)
        self.assertAlmostEqual(result["pca"]["explained_variance_ratio"][0], 1)
        np.testing.assert_allclose(result["pca"]["eigenvectors"][0][:2], [1 / np.sqrt(5), 2 / np.sqrt(5)], atol=1e-12)

    def test_spherical_shrinkage_matches_hand_calculation(self):
        result = estimate_covariance(history(), "2020-01-04", lookback_changes=2, shrinkage=.2)
        expected = np.zeros((9, 9)); expected[:2, :2] = [[.4, .8], [.8, 1.6]]
        expected += np.eye(9) / 18  # .2 * sample trace2.5 /9.
        np.testing.assert_allclose(result["covariance"], expected, atol=1e-12)

    def test_execution_and_future_levels_cannot_supply_training_endpoints(self):
        original = history()
        future = [{"date": "2020-01-04", "zero_yields_bps": [9999] * 9},
                  {"date": "2020-01-05", "zero_yields_bps": [float("nan")] * 9}]
        baseline = estimate_covariance(original, "2020-01-04", lookback_changes=2)
        result = estimate_covariance(original + future, "2020-01-04", lookback_changes=2)
        self.assertEqual(result["covariance"], baseline["covariance"])
        self.assertEqual(result["training_end_date"], "2020-01-03")
        self.assertEqual(result["excluded_on_or_after_execution"], 2)
        self.assertEqual(original, history())

    def test_full_prior_window_gap_duplicate_and_missing_data_controls(self):
        gap = history(); gap[-1]["date"] = "2020-01-20"
        duplicate = history(); duplicate[-1]["date"] = "2020-01-02"
        nonfinite = history(); nonfinite[0]["zero_yields_bps"][0] = float("inf")
        for rows, execution, parameters in ((history()[:2], "2020-01-04", {}), (gap, "2020-01-21", {}),
            (duplicate, "2020-01-04", {}), (nonfinite, "2020-01-04", {}), (history(), "2020-01-03", {}),
            (history(), "2020-01-04", {"shrinkage": 1.01}), (history(), "2020-01-04", {"max_gap_days": 0})):
            with self.subTest(rows=rows, parameters=parameters), self.assertRaises(ValueError):
                estimate_covariance(rows, execution, lookback_changes=2, **parameters)


if __name__ == "__main__":
    unittest.main()
