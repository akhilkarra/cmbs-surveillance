"""Serve a pinned local Clef model at http://127.0.0.1:<port>/v1/systemone, one request at a time.

Usage:
  uv sync --extra local-model
  uv run python tools/clef_server.py [--model clef-flash-2026-10-01] [--port 30000] [--device mps]
  export CMBS_CLEF_URL=http://127.0.0.1:30000

The model loads once from the checked Hugging Face cache (never downloaded) and answers each
request at batch size 1 through `cmbs.model.local.InProcess`, the same path a run uses when no
server is named, so every answer names the pinned revision and reference-code hash, and a
request for any other model is refused.

It listens on the loopback address only and refuses what a browser page could send it: a Host
other than this address and port (DNS rebinding), a body that is not JSON, or one over 1 MB.
The pipeline treats it as a model server all the same: restricted text is never sent to it
(`cmbs.model.policy.check_may_send`). Any Python with torch and transformers can run it: the
module needs only the standard library and `cmbs.model.local` (put `src` on PYTHONPATH outside
`uv run`).
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Any

from cmbs.model.local import CLEF_FLASH_2026_10_01, InProcess, local_model

PATH = "/v1/systemone"
MAX_BODY = 1 << 20  # 1 MB: a comment and its questions are a few kilobytes
HOSTS = ("127.0.0.1", "localhost", "[::1]")

type Answer = Callable[[dict[str, Any]], dict[str, Any]]


def allowed_host(header: str | None, port: int) -> bool:
    """The Host header names this loopback address and port, and nothing else."""
    return header in {f"{host}:{port}" for host in HOSTS}


def make_server(answer: Answer, port: int) -> ThreadingHTTPServer:
    lock = Lock()  # one request at a time: batch size 1, and one model on the GPU

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != PATH:
                self._reply(404, {"error": f"POST {PATH}"})
                return
            if not allowed_host(self.headers.get("Host"), server.server_port):
                self._reply(403, {"error": "the Host header must name this server"})
                return
            content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self._reply(415, {"error": "send application/json"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._reply(413, {"error": f"the body is over {MAX_BODY} bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
                started = time.perf_counter()
                with lock:
                    reply = answer(body)
                elapsed = time.perf_counter() - started
                print(f"answered {len(body['questions'])} questions in {elapsed:.2f} s", flush=True)
            except (ValueError, KeyError, TypeError) as error:
                self._reply(400, {"error": str(error)})
                return
            self._reply(200, reply)

        def _reply(self, status: int, payload: object) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            pass  # the answer line above is the log

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--model", default=CLEF_FLASH_2026_10_01.name)
    parser.add_argument("--port", type=int, default=30000)
    parser.add_argument("--device", default=None, help="mps, cuda or cpu (default: the best)")
    args = parser.parse_args(argv)
    model = InProcess(local_model(args.model), device=args.device)
    started = time.perf_counter()
    model({"model": args.model, "state": "warm-up", "questions": {"ok": {"type": "noul"}}})
    print(f"loaded {model.path} in {time.perf_counter() - started:.1f} s", flush=True)
    server = make_server(model, args.port)
    print(f"serving {args.model} at http://127.0.0.1:{args.port}{PATH}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
