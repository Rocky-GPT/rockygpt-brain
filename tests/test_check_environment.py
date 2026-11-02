import sys
from importlib import metadata
from pathlib import Path

import check_environment as check

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_names_the_python_production_runs() -> None:
    assert check.expected_python(ROOT / "Dockerfile") == "3.13"


def test_lock_markers_skip_other_platforms(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("fastapi==1.0 \\\n    --hash=sha256:aa\n"
                    "colorama==0.4.6 ; sys_platform == 'win32' \\\n    --hash=sha256:bb\n"
                    "Typing_Extensions==4.1 \\\n")
    found = check.locked_packages(lock)
    assert found["fastapi"] == "1.0"
    assert found["typing-extensions"] == "4.1"
    assert ("colorama" in found) == (sys.platform == "win32")


def test_each_difference_is_named() -> None:
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    pytest_version = metadata.version("pytest")
    found = check.differences("2.7", {"pytest": pytest_version, "no-such-package-xyz": "1.0",
                                      "pip": "0.0.1"})
    assert found[0] == f"Python {running}, production runs 2.7"
    assert "no-such-package-xyz missing, production has 1.0" in found
    assert any(line.startswith("pip ") for line in found)
    assert not any(line.startswith("pytest") for line in found)


def test_lock_covers_every_runtime_dependency() -> None:
    """A dependency added to pyproject.toml without regenerating the lock fails here."""
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    wanted = project["dependencies"] + project["optional-dependencies"]["graph"]
    names = {check.canonical(re_name) for re_name in (
        spec.split(";")[0].split("[")[0].split("=")[0].split(">")[0].split("<")[0].strip()
        for spec in wanted)}
    assert names <= set(check.locked_packages(ROOT / "requirements.lock"))
