"""The dashboard scenarios' bundle build (features/steps/dashboard.py): when it rebuilds, and how
it fails without Docker."""

import os
import subprocess
from pathlib import Path

import pytest

from features.steps import dashboard as harness


def make_web(root: Path) -> Path:
    web = root / "web"
    (web / "src").mkdir(parents=True)
    (web / "dist").mkdir()
    for name in harness.BUNDLE_CONFIG:
        (web / name).write_text("{}")
    for name in ("App.tsx", "App.test.tsx", "test-data.ts", "styles.css"):
        (web / "src" / name).write_text("")
    (web / "dist" / "index.html").write_text("<html></html>")
    return web


def age(path: Path, seconds: int) -> None:
    """Set the file's modification time `seconds` into the past (negative: the future)."""
    stamp = path.stat().st_mtime - seconds
    os.utime(path, (stamp, stamp))


def test_the_bundle_is_built_from_its_configs_and_sources_but_not_tests(tmp_path):
    web = make_web(tmp_path)
    names = sorted(path.relative_to(web).as_posix() for path in harness.bundle_sources(web))
    assert names == [
        "index.html",
        "package-lock.json",
        "package.json",
        "src/App.tsx",
        "src/styles.css",
        "tsconfig.json",
        "vite.config.ts",
    ]


@pytest.mark.parametrize("changed", ["tsconfig.json", "package.json", "src/App.tsx"])
def test_a_newer_config_or_source_makes_the_bundle_stale(tmp_path, changed):
    web = make_web(tmp_path)
    for path in harness.bundle_sources(web):
        age(path, 100)
    assert harness.bundle_is_fresh(web)
    age(web / changed, -200)
    assert not harness.bundle_is_fresh(web)


def test_a_newer_test_file_does_not(tmp_path):
    web = make_web(tmp_path)
    for path in harness.bundle_sources(web):
        age(path, 100)
    age(web / "src" / "App.test.tsx", -200)
    assert harness.bundle_is_fresh(web)


def test_no_bundle_is_never_fresh(tmp_path):
    web = make_web(tmp_path)
    (web / "dist" / "index.html").unlink()
    assert not harness.bundle_is_fresh(web)


def test_without_docker_the_build_says_so(tmp_path):
    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    with pytest.raises(AssertionError) as failed:
        harness.build_bundle(run=missing)
    assert str(failed.value) == harness.DOCKER_MISSING
    assert "the docker command was not found" in harness.DOCKER_MISSING


def test_a_failed_build_shows_its_output():
    def fails(command, **kwargs):
        assert command[:3] == ["docker", "compose", "run"]
        return subprocess.CompletedProcess(command, 1, stdout="vite: ", stderr="type error")

    with pytest.raises(AssertionError) as failed:
        harness.build_bundle(run=fails)
    assert str(failed.value).endswith("vite: type error")
    assert str(failed.value).startswith("building the dashboard failed")


def test_a_build_that_succeeds_says_nothing():
    calls = []

    def works(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    harness.build_bundle(run=works)
    assert calls == [
        ["docker", "compose", "run", "--rm", "--no-deps", "web", "sh", "-c", harness.BUILD]
    ]
