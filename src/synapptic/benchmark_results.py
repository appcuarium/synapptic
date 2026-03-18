"""Benchmark result listing and comparison.

Results are saved by benchmark.save_benchmark() into BENCHMARKS_DIR.
This module provides read-only queries over those saved results.
"""

import json
from datetime import datetime

from synapptic.config import SYNAPPTIC_DIR

BENCHMARKS_DIR = SYNAPPTIC_DIR / "benchmarks"


def list_benchmark_results(provider: str | None = None, model: str | None = None) -> list[dict]:
    """List saved benchmark results, optionally filtered by provider/model.

    Reads from BENCHMARKS_DIR, skipping test cache files (*_tests_*).
    """
    if not BENCHMARKS_DIR.exists():
        return []

    results = []
    for filepath in sorted(BENCHMARKS_DIR.glob("*.json"), reverse=True):
        if "_tests_" in filepath.name or filepath.name.endswith("_tests.json"):
            continue
        try:
            with open(filepath) as f:
                data = json.load(f)

            if provider and data.get("provider") != provider:
                continue
            if model and data.get("model") != model:
                continue

            data["_filepath"] = str(filepath)
            results.append(data)
        except (json.JSONDecodeError, IOError):
            continue

    return results


def compare_results(
    provider1: str, model1: str, provider2: str, model2: str, project: str | None = None
) -> dict:
    """Compare benchmark results between two provider/model combinations."""
    results1 = _find_latest_result(provider1, model1, project)
    results2 = _find_latest_result(provider2, model2, project)

    if not results1 or not results2:
        return {"error": "Could not find results for comparison"}

    return {
        "timestamp": datetime.now().isoformat(),
        "compared": [
            {"provider": provider1, "model": model1, "timestamp": results1.get("timestamp")},
            {"provider": provider2, "model": model2, "timestamp": results2.get("timestamp")},
        ],
        "summary1": results1.get("summary", {}),
        "summary2": results2.get("summary", {}),
    }


def _find_latest_result(provider: str, model: str, project: str | None = None) -> dict | None:
    """Find the most recent benchmark result for a provider/model."""
    all_results = list_benchmark_results(provider, model)
    for result in all_results:
        if project is None or result.get("project") == (project or "global"):
            return result
    return None
