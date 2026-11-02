"""Check that this Python matches what the production image runs: the same Python minor version
(the Dockerfile's base image) and every package at its requirements.lock version.

    .venv/bin/python scripts/check_environment.py      # exit 1 and list each difference

The laptop launcher (rockygpt-workspace run-local.sh) runs this before starting the Brain and
installs the lock when only packages differ; CI runs it so tests use production's versions.
"""

import re
import sys
from importlib import metadata
from pathlib import Path

try:
    from packaging.markers import Marker
except ImportError:  # pip vendors packaging in every virtualenv that has pip
    from pip._vendor.packaging.markers import Marker  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]


def expected_python(dockerfile: Path) -> str:
    found = re.findall(r"^FROM python:(\d+\.\d+)", dockerfile.read_text(), re.MULTILINE)
    if not found or len(set(found)) != 1:
        raise SystemExit("The Dockerfile does not name one python:X.Y base image.")
    return str(found[0])


def locked_packages(lock: Path) -> dict[str, str]:
    """name -> version for every lock entry whose environment marker applies here."""
    packages = {}
    for line in lock.read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9._-]+)==([^\s;\\]+)\s*(?:;\s*([^\\]+?))?\s*\\?$", line)
        if match and (match[3] is None or Marker(match[3]).evaluate()):
            packages[canonical(match[1])] = match[2]
    if not packages:
        raise SystemExit(f"{lock.name} lists no packages.")
    return packages


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def differences(python: str, packages: dict[str, str]) -> list[str]:
    found = []
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if running != python:
        found.append(f"Python {running}, production runs {python}")
    for name, version in sorted(packages.items()):
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            found.append(f"{name} missing, production has {version}")
            continue
        if installed != version:
            found.append(f"{name} {installed}, production has {version}")
    return found


def main() -> int:
    python = expected_python(ROOT / "Dockerfile")
    found = differences(python, locked_packages(ROOT / "requirements.lock"))
    for line in found:
        print(line)
    if not found:
        print("Matches production: same Python and package versions.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
