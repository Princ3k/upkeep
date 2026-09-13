"""A local server for watching extraction runs as they happen.

Two things this is for, in order of importance.

The second is watching: a run takes minutes, and per-guide results arriving live
— including every change dropped as ungrounded, with the text that was not in
the source — beats staring at a silent terminal.

The first is variance. A single pass per extractor turned out to be worthless
for comparing them: the same model on the same prompt over the same three guides
produced 0 call patterns one time and 13 another. So this runs a configuration
`repeat` times and reports the spread, which is the measurement that was missing.

    pip install -e '.[extract]'
    export GEMINI_API_KEY=...          # or ANTHROPIC_API_KEY
    python evals/serve.py              # http://127.0.0.1:8765

Standard library only — no web framework — so watching a run costs the project
no dependency it would not otherwise carry.
"""

from __future__ import annotations

import argparse
import json
import queue
import statistics
import threading
import traceback
import uuid
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from upkeep.detect.from_guide import BACKENDS, spec_from_guide

ROOT = Path(__file__).parent
RUNS: dict[str, "Run"] = {}


class Run:
    """One configuration, executed `repeat` times, streamed to any listeners."""

    def __init__(self, config: dict) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.config = config
        self.events: list[dict] = []
        self.listeners: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.done = False

    def emit(self, event: dict) -> None:
        with self.lock:
            self.events.append(event)
            listeners = list(self.listeners)
        for listener in listeners:
            listener.put(event)

    def listen(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            for event in self.events:  # replay so a late tab sees the whole run
                q.put(event)
            self.listeners.append(q)
        return q

    def drop(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)


def guides_for(selection: list[str] | None) -> list[dict]:
    manifest = json.loads((ROOT / "corpus" / "manifest.json").read_text())
    entries = manifest["guides"]
    if selection:
        wanted = set(selection)
        entries = [e for e in entries if e["file"][:-3] in wanted]
    return entries


def execute(run: Run) -> None:
    config = run.config
    entries = guides_for(config.get("guides"))
    repeats = max(1, int(config.get("repeat", 1)))
    backend_cls = BACKENDS[config["provider"]]
    backend = backend_cls(model=config["model"]) if config.get("model") else backend_cls()

    run.emit({
        "type": "start", "provider": backend.name, "model": backend.model,
        "guides": [e["file"][:-3] for e in entries], "repeat": repeats,
        "total": len(entries) * repeats,
    })

    out_dir = ROOT / "runs" / (config.get("out") or f"live-{run.id}")
    per_guide: dict[str, list[dict]] = {}
    index = 0

    for pass_no in range(1, repeats + 1):
        for entry in entries:
            stem = entry["file"][:-3]
            index += 1
            run.emit({"type": "guide_start", "guide": stem, "pass": pass_no,
                      "index": index})
            try:
                text = (ROOT / "corpus" / entry["file"]).read_text()
                result = spec_from_guide(
                    text, provider=entry["provider"], from_version=entry["from"],
                    to_version=entry["to"], backend=backend,
                )
            except Exception as exc:  # a failed guide must not kill the run
                run.emit({"type": "guide_error", "guide": stem, "pass": pass_no,
                          "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue

            kinds = Counter(c.kind for c in result.spec.changes)
            summary = {
                "type": "guide_done", "guide": stem, "pass": pass_no, "index": index,
                "changes": len(result.spec.changes), "kinds": dict(kinds),
                "malformed": result.malformed, "dropped": result.dropped,
                "severity": result.spec.severity.value,
                "drops": [
                    {"label": d.label, "missing": d.missing[:2]}
                    for d in result.grounding.fabricated
                ],
            }
            per_guide.setdefault(stem, []).append(summary)
            run.emit(summary)

            if pass_no == 1:
                target = out_dir
                target.mkdir(parents=True, exist_ok=True)
                (target / f"{stem}.json").write_text(
                    json.dumps(result.spec.model_dump(by_alias=True, mode="json"),
                               indent=2) + "\n"
                )

    spread = []
    for stem, passes in sorted(per_guide.items()):
        counts = [p["changes"] for p in passes]
        patterns = [p["kinds"].get("call_pattern_changed", 0) for p in passes]
        spread.append({
            "guide": stem, "runs": len(counts),
            "changes": counts, "min": min(counts), "max": max(counts),
            "mean": round(statistics.mean(counts), 1),
            "stdev": round(statistics.stdev(counts), 2) if len(counts) > 1 else 0.0,
            "patterns": patterns,
        })

    run.emit({"type": "done", "out": str(out_dir.relative_to(ROOT)),
              "spread": spread, "repeat": repeats})
    run.done = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep the console for run output
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            page = (ROOT / "live.html").read_bytes()
            return self._send(200, page, "text/html; charset=utf-8")

        if parsed.path == "/api/config":
            manifest = json.loads((ROOT / "corpus" / "manifest.json").read_text())
            import os
            return self._json({
                "guides": [
                    {"stem": g["file"][:-3], "provider": g["provider"],
                     "language": g["language"]}
                    for g in manifest["guides"]
                ],
                "providers": sorted(BACKENDS),
                "keys": {
                    "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")
                                      or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
                    "google": bool(os.environ.get("GEMINI_API_KEY")
                                   or os.environ.get("GOOGLE_API_KEY")),
                },
            })

        if parsed.path == "/api/events":
            run_id = parse_qs(parsed.query).get("run", [""])[0]
            run = RUNS.get(run_id)
            if not run:
                return self._json({"error": "unknown run"}, 404)
            return self._stream(run)

        return self._json({"error": "not found"}, 404)

    def _stream(self, run: Run) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = run.listen()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")  # keeps proxies from closing
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
                if event.get("type") == "done":
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            run.drop(q)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/run":
            return self._json({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            config = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        if config.get("provider") not in BACKENDS:
            return self._json({"error": "unknown provider"}, 400)

        run = Run(config)
        RUNS[run.id] = run

        def target() -> None:
            try:
                execute(run)
            except Exception:
                run.emit({"type": "fatal", "error": traceback.format_exc()[-600:]})
                run.done = True

        threading.Thread(target=target, daemon=True).start()
        return self._json({"run": run.id})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="Open a browser.")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"upkeep live  →  {url}")
    print("  runs cost real money; the page shows the estimate before you start.")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
