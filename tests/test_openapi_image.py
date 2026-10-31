"""The api service's image (Dockerfile, grill log Q19) stays pinned and watched.

Every base image is pinned by digest, Dependabot's `docker` ecosystem bumps them, and the node
image that builds the dashboard is the web service's own, so the bundle is built the same way in
both places.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROM = re.compile(r"^FROM\s+(\S+)", re.MULTILINE)
PINNED = re.compile(r"^[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}$")


def base_images() -> list[str]:
    return FROM.findall((ROOT / "Dockerfile").read_text(encoding="utf-8"))


def test_every_base_image_is_pinned_by_digest():
    images = base_images()
    assert [image.split(":")[0] for image in images] == [
        "node",
        "ghcr.io/astral-sh/uv",
        "python",
    ]
    assert all(PINNED.match(image) for image in images), images


def test_the_dashboard_is_built_in_the_web_services_node_image():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    web = re.search(r"^\s+image:\s+(node:\S+)", compose, re.MULTILINE)
    assert web is not None
    assert base_images()[0] == web.group(1)


def test_dependabot_bumps_the_dockerfile():
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    entry = re.search(r"- package-ecosystem: docker\n\s+directory: /\n", config)
    assert entry is not None, "add a `docker` ecosystem for the Dockerfile to dependabot.yml"
