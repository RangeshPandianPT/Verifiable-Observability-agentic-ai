import json
import os
from pathlib import Path

import pytest
import yaml

from verifiable_observability.eval.harness import EvalHarness
from verifiable_observability.storage.db import create_db_engine


@pytest.fixture
def temp_config_path(tmp_path):
    config_data = {
        "experiment": {
            "name": "test_sweep",
            "domains": ["finance"],
            "regimes": ["compliant", "mild_drift"],
            "agent_backends": [],
            "n_trajectories_per_cell": 1,
            "output_dir": str(tmp_path / "results")
        }
    }
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)
    return str(config_file)


@pytest.fixture
def temp_db_path(tmp_path):
    db_file = tmp_path / "test.db"
    # Create empty db
    create_db_engine(str(db_file))
    return str(db_file)


def test_eval_harness_regimes(temp_config_path, temp_db_path, tmp_path):
    harness = EvalHarness(config_path=temp_config_path, db_path=temp_db_path)
    
    report_path_str = harness.run_sweep()
    report_path = Path(report_path_str)
    
    assert report_path.exists()
    
    with open(report_path, "r") as f:
        results = json.load(f)
        
    assert len(results) == 2  # 1 domain * 2 regimes * 1 run
    
    # Assert contents
    domains_in_results = {r["domain"] for r in results}
    regimes_in_results = {r["regime"] for r in results}
    
    assert domains_in_results == {"finance"}
    assert regimes_in_results == {"compliant", "mild_drift"}
    
    for r in results:
        assert "avg_rcr" in r
        assert "avg_ccr" in r
        assert r["outcome"] in ["completed", "truncated", "blocked", "failed", "in_progress"]
