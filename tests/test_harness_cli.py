"""`synapptic harness` is plumbing around the separately installed synapptic-core package: without it, the
commands say so (the one message every module uses) instead of failing somewhere inside."""

from click.testing import CliRunner

from synapptic import core as core_module
from synapptic.cli import cli


def test_harness_mine_says_when_the_core_package_is_missing(monkeypatch, tmp_path):
    # boundary: the compiled core is a separately installed package; here it is absent
    monkeypatch.setattr(core_module, "core", None)
    transcript = tmp_path / "s.jsonl"
    transcript.write_text("")
    result = CliRunner().invoke(cli, ["harness", "mine", "-s", str(transcript)])
    assert result.exit_code != 0
    assert "synapptic-core package is not installed" in result.output
