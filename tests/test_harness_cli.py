"""`synapptic harness` is plumbing around the separately installed synapptic-harness package: without it, the
commands say so instead of failing somewhere inside."""

from click.testing import CliRunner

from synapptic import harness
from synapptic.cli import cli


def test_harness_mine_says_when_the_core_package_is_missing(monkeypatch, tmp_path):
    # boundary: the compiled core is an optional, separately installed package; here it is absent
    monkeypatch.setattr(harness, "core", None)
    transcript = tmp_path / "s.jsonl"
    transcript.write_text("")
    result = CliRunner().invoke(cli, ["harness", "mine", "-s", str(transcript)])
    assert result.exit_code != 0
    assert "synapptic-harness package is not installed" in result.output
