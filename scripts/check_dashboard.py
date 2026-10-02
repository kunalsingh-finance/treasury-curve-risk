"""Offline AppTest smoke; requires the local pinned raw data and verified release.

Run ``python scripts/check_dashboard.py`` after building the release. This
checks real local analysis plus mocked saved-release failures; no network,
browser, repository data changes, or regenerated artifacts are required.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from scripts.verify_release import load_verified_release


def require(condition, description):
    if not condition:
        raise AssertionError(description)


def healthy(app, phase):
    require(not app.exception, f"{phase}: app exception: {[item.message for item in app.exception]}")


def metric(app, label):
    return next(item.value for item in app.metric if item.label == label)


def run_checks() -> dict:
    require((ROOT / "data/raw/feds200628.csv").is_file(), "Pinned local curve data missing; acquire it before this offline check")
    _, verification = load_verified_release(ROOT / "outputs/release")
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    healthy(app, "normal")
    require(not app.error, "Normal portfolio analysis unexpectedly failed")
    require(metric(app, "Solver status") == "optimal", "Normal optimizer did not certify a hedge")
    require(any(item.label == "Historical evaluation year" for item in app.selectbox), "Verified history is not visible")
    require(len(app.dataframe) == 6, "Normal view lacks portfolio, hedge, scenario, historical or evidence tables")
    original_price = metric(app, "Dated portfolio dirty PV")

    app.number_input[0].set_value(3.0).run()
    healthy(app, "face change")
    changed_price = metric(app, "Dated portfolio dirty PV")
    require(changed_price != original_price, "Editing face amount failed to reprice the portfolio")
    require(metric(app, "Solver status") == "optimal", "Face edit unexpectedly failed optimization")

    app.checkbox[0].set_value(True)
    app.slider[0].set_value(.1)
    app.run()
    healthy(app, "infeasible")
    require(metric(app, "Solver status") == "infeasible", "Tight cash-neutral constraints did not report infeasibility")
    require(any("No valid constrained hedge" in item.value for item in app.error), "Infeasible hedge lacks an explicit failure message")
    require(not app.success, "Infeasible view retained a prior success message")
    scenario = next(item.value for item in app.dataframe if {"name", "constrained"}.issubset(item.value.columns))
    require(scenario["constrained"].isna().all(), "Infeasible analysis retained constrained scenario positions")
    require(not any("constrained_face" in item.value.columns for item in app.dataframe), "Infeasible analysis retained the hedge-position table")

    app.checkbox[0].set_value(False)
    app.slider[0].set_value(2.0)
    app.run()
    healthy(app, "restored controls")
    require(metric(app, "Solver status") == "optimal", "Restored controls did not produce a fresh valid hedge")

    unavailable = (
        ("malformed release", json.JSONDecodeError("Synthetic truncated saved release", '{"status":', 10)),
        ("stale release", ValueError("Synthetic saved release model/config/source provenance is stale")),
    )
    for phase, error in unavailable:
        # app.py imports the patched function at each AppTest script rerun.
        with patch("scripts.verify_release.load_verified_release", side_effect=error):
            app.run()
        healthy(app, phase)
        require(metric(app, "Solver status") == "optimal", f"{phase}: current portfolio analysis became unavailable")
        require(any("Build the verified dated-history release" in item.value for item in app.warning), f"{phase}: saved-release warning missing")
        require(not any(item.label == "Historical evaluation year" for item in app.selectbox), f"{phase}: stale history remains available")
        require(len(app.dataframe) == 4, f"{phase}: stale historical or auction-evidence tables remain visible")

    return {"status": "passed", "phases": ["normal", "face_change", "infeasible", "restored_controls",
                                            "malformed_release", "stale_release"],
            "original_dirty_pv": original_price, "changed_dirty_pv": changed_price,
            "release_verification": verification}


if __name__ == "__main__":
    print(json.dumps(run_checks(), indent=2))
