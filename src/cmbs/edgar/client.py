"""HTTP access to SEC EDGAR hosts with a declared User-Agent.

SEC asks automated clients to identify themselves. Set SEC_USER_AGENT to a string with a
project contact address you control. www.sec.gov rejects the default with HTTP 403, while
data.sec.gov and efts.sec.gov accept it (tested 2026-10-24).
"""

import json
import os
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_USER_AGENT = "cmbs-surveillance-research (academic project)"
_SYSTEM_CA_BUNDLE = Path("/etc/ssl/cert.pem")


def user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT)


def _ssl_context() -> ssl.SSLContext:
    if _SYSTEM_CA_BUNDLE.is_file():
        return ssl.create_default_context(cafile=str(_SYSTEM_CA_BUNDLE))
    return ssl.create_default_context()


def get_bytes(url: str, *, timeout: float = 60) -> bytes:
    headers = {"User-Agent": user_agent(), "Accept-Encoding": "identity"}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        return response.read()


def get_json(url: str, *, timeout: float = 30) -> Any:
    return json.loads(get_bytes(url, timeout=timeout))


def pause(seconds: float) -> None:
    """Sleep between requests to stay well under SEC's 10 requests per second."""
    time.sleep(seconds)
