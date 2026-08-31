"""Proves the package imports and the toolchain runs. Replace as the brain grows."""

import rockygpt_brain


def test_package_imports() -> None:
    assert rockygpt_brain.__version__ == "0.0.0"
