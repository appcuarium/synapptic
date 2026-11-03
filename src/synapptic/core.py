"""The synapptic-core package: synapptic's learning logic, compiled and installed separately.

Every module that learns (filter, extract, profile, synthesize, benchmark, harness) imports it through
here, so a missing or incompatible core fails with one clear message instead of an error somewhere inside.
"""

import hashlib
from pathlib import Path

try:
    import synapptic_core as core
except ImportError:
    core = None

# The core versions this synapptic can work with: same major and minor line. 0.2 brought the harness hooks
# and the rules store into the core; a 0.1 core has neither.
CORE_REQUIRED = ((0, 2), (0, 3))   # >= 0.2, < 0.3
NOT_INSTALLED = ("the synapptic-core package is not installed; synapptic needs it to learn from sessions "
                 "(pip install synapptic-core)")


class CoreMissing(RuntimeError):
    pass


def version_tuple(version: str) -> tuple[int, ...]:
    """The numeric parts of a version string ("0.1.0" -> (0, 1, 0)); anything else stops the parse."""
    parts = []
    for piece in str(version).split("."):
        digits = ""
        for char in piece:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def compatible(version: str) -> bool:
    """Whether a core version falls in CORE_REQUIRED."""
    found = version_tuple(version)
    low, high = CORE_REQUIRED
    return bool(found) and low <= found[:2] < high


def require_core():
    if core is None:
        raise CoreMissing(NOT_INSTALLED)
    version = getattr(core, "__version__", "")
    if not compatible(version):
        low, high = CORE_REQUIRED
        raise CoreMissing(f"synapptic needs synapptic-core >={low[0]}.{low[1]},<{high[0]}.{high[1]}; "
                          f"found {version or 'an unknown version'} (pip install --upgrade synapptic-core)")
    return core


def core_binary() -> Path | None:
    """The installed core's compiled module, or None when it is not installed."""
    if core is None:
        return None
    try:
        return Path(core._core.__file__)
    except AttributeError:
        return None


def core_info() -> dict:
    """What `synapptic config show` prints: the core's version and the SHA-256 of its binary, to compare
    with the checksums published with each release; or installed False."""
    binary = core_binary()
    if binary is None or not binary.is_file():
        return {"installed": core is not None, "version": getattr(core, "__version__", ""), "sha256": "", "path": ""}
    return {"installed": True, "version": getattr(core, "__version__", ""),
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "path": str(binary)}
