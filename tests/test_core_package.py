"""synapptic.core: the one door to the compiled synapptic-core package. A missing core and an incompatible
core each give one clear message; `config show` reports the installed core's version and the SHA-256 of
its binary so it can be checked against the checksums published with the release."""

import hashlib
import types

import pytest
from click.testing import CliRunner

from synapptic import core as core_module
from synapptic.cli import cli
from synapptic.core import CoreMissing, core_info, require_core


def fake_core(tmp_path, version, body=b"compiled bytes"):
    """A stand-in for the installed package: a version and a compiled module file."""
    # boundary: synapptic-core is a separately installed, compiled package; this is only its shape
    binary = tmp_path / "_core.abi3.so"
    binary.write_bytes(body)
    package = types.SimpleNamespace(__version__=version, _core=types.SimpleNamespace(__file__=str(binary)))
    return package, binary


def test_an_incompatible_core_version_is_refused_with_a_clear_message(monkeypatch, tmp_path):
    package, _ = fake_core(tmp_path, "9.0.0")
    monkeypatch.setattr(core_module, "core", package)
    with pytest.raises(CoreMissing, match=r"needs synapptic-core >=0\.1,<0\.2; found 9\.0\.0"):
        require_core()


def test_a_compatible_core_version_passes(monkeypatch, tmp_path):
    package, _ = fake_core(tmp_path, "0.1.7")
    monkeypatch.setattr(core_module, "core", package)
    assert require_core() is package


def test_config_show_prints_the_core_version_and_binary_hash(monkeypatch, tmp_path):
    package, binary = fake_core(tmp_path, "0.1.0", b"the bytes pip installed")
    monkeypatch.setattr(core_module, "core", package)
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "config.yaml")   # boundary: filesystem
    expected = hashlib.sha256(b"the bytes pip installed").hexdigest()
    assert core_info()["sha256"] == expected
    result = CliRunner().invoke(cli, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert f"synapptic-core 0.1.0" in result.output and expected in result.output


def test_config_show_says_when_the_core_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(core_module, "core", None)
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "config.yaml")   # boundary: filesystem
    result = CliRunner().invoke(cli, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "not installed" in result.output
