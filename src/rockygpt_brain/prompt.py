from __future__ import annotations

from pathlib import Path


def beside(module_file: str) -> str:
    return (Path(module_file).parent / "prompt.md").read_text(encoding="utf-8").strip()
