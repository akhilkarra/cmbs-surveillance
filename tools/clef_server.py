"""Serve a pinned local Clef model at http://127.0.0.1:<port>/v1/systemone, one request at a time.

Usage:
  uv sync --extra local-model
  uv run python tools/clef_server.py [--model clef-flash-2026-10-01] [--port 30000] [--device mps]
  export CMBS_CLEF_URL=http://127.0.0.1:30000

The model loads once from the Hugging Face cache (never downloaded) and answers each request
with the reference code at batch size 1, through `cmbs.model.local.InProcess`, the same path a
run uses when no server is named. It listens on the loopback address only, so text sent to it
never leaves this machine. Any Python with torch and transformers can run it: the module needs
only the standard library and `cmbs.model.local` (put `src` on PYTHONPATH outside `uv run`).
"""

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

from cmbs.model.local import CLEF_FLASH_2026_10_01, InProcess, local_model

PATH = "/v1/systemone"


def serve(model: InProcess, port: int) -> None:
    lock = Lock()  # one request at a time: batch size 1, and one model on the GPU

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != PATH:
                self._reply(404, {"error": f"POST {PATH}"})
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                started = time.perf_counter()
                with lock:
                    answer = model(body)
                elapsed = time.perf_counter() - started
                print(f"answered {len(body['questions'])} questions in {elapsed:.2f} s", flush=True)
            except (ValueError, KeyError, TypeError) as error:
                self._reply(400, {"error": str(error)})
                return
            self._reply(200, answer)

        def _reply(self, status: int, payload: object) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"serving {model.model.name} at http://127.0.0.1:{port}{PATH}", flush=True)
    server.serve_forever()


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
    serve(model, args.port)


if __name__ == "__main__":
    main(sys.argv[1:])
