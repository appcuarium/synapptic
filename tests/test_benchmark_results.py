"""Tests for benchmark_results module."""

import json

import pytest

from synapptic.benchmark_results import compare_results, list_benchmark_results


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("synapptic.benchmark_results.BENCHMARKS_DIR", tmp_path)
    return tmp_path


def _write_result(path, provider, model, project="global", summary=None):
    data = {
        "provider": provider,
        "model": model,
        "project": project,
        "timestamp": "2026-03-25T10:00:00Z",
        "summary": summary or {"guard_compliance": 0.7},
        "tests": [],
    }
    path.write_text(json.dumps(data))
    return data


class TestListBenchmarkResults:

    def test_empty_dir_returns_empty(self, results_dir):
        assert list_benchmark_results() == []

    def test_lists_result_files(self, results_dir):
        _write_result(results_dir / "result1.json", "anthropic", "claude-3-haiku-20240307")
        _write_result(results_dir / "result2.json", "ollama", "llama3")
        results = list_benchmark_results()
        assert len(results) == 2

    def test_skips_test_cache_files(self, results_dir):
        _write_result(results_dir / "result1.json", "anthropic", "sonnet")
        (results_dir / "global_anthropic_sonnet_tests_seed42.json").write_text(
            json.dumps({"tests": []})
        )
        results = list_benchmark_results()
        assert len(results) == 1

    def test_filters_by_provider(self, results_dir):
        _write_result(results_dir / "r1.json", "anthropic", "sonnet")
        _write_result(results_dir / "r2.json", "ollama", "llama3")
        results = list_benchmark_results(provider="anthropic")
        assert len(results) == 1
        assert results[0]["provider"] == "anthropic"

    def test_filters_by_model(self, results_dir):
        _write_result(results_dir / "r1.json", "anthropic", "sonnet")
        _write_result(results_dir / "r2.json", "anthropic", "opus")
        results = list_benchmark_results(model="opus")
        assert len(results) == 1
        assert results[0]["model"] == "opus"

    def test_skips_malformed_json(self, results_dir):
        (results_dir / "broken.json").write_text("{ not valid json")
        results = list_benchmark_results()
        assert results == []

    def test_filepath_added_to_result(self, results_dir):
        _write_result(results_dir / "result1.json", "anthropic", "sonnet")
        results = list_benchmark_results()
        assert "_filepath" in results[0]


class TestCompareResults:

    def test_compare_found_results(self, results_dir):
        _write_result(results_dir / "r1.json", "anthropic", "sonnet",
                      summary={"guard_compliance": 0.8})
        _write_result(results_dir / "r2.json", "ollama", "llama3",
                      summary={"guard_compliance": 0.5})
        result = compare_results("anthropic", "sonnet", "ollama", "llama3")
        assert "error" not in result
        assert result["summary1"]["guard_compliance"] == 0.8
        assert result["summary2"]["guard_compliance"] == 0.5

    def test_missing_result_returns_error(self, results_dir):
        result = compare_results("anthropic", "sonnet", "ollama", "llama3")
        assert "error" in result
