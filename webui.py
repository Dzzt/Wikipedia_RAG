#!/usr/bin/env python3
"""Local Wikipedia RAG Web UI."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import time
import traceback
import webbrowser
from collections import defaultdict
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from typing import Any, Iterable
from urllib.parse import urlparse

import ollama
from wikirag.article_viewers import ArticleViewer, create_article_viewer
from wikirag.search_engine import SearchEngine

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_INDEX = Path("index")
DEFAULT_PROMPT_DIR = BASE_DIR / "prompts"
DEFAULT_TEMPLATE_DIR = BASE_DIR / "templates"
DEFAULT_STATIC_DIR = BASE_DIR / "static"
DEFAULT_JSONL_VIEWER_DIR = BASE_DIR / "tools" / "wikipedia_viewer"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DEFAULT_MODEL = "Mistral-Nemo-Japanese"
DEFAULT_TOP_K = 8
DEFAULT_CONTEXT_CHARS = 12_000
DEFAULT_CONTEXT_LENGTH = 12_288
DEFAULT_NUM_PREDICT = 4_096
DEFAULT_TEMPERATURE = 0.5
DEFAULT_THINK_MODE = "auto"
MAX_REQUEST_BYTES = 1_000_000

BROWSER_CLOSE_GRACE_SECONDS = 5.0
BROWSER_HEARTBEAT_TIMEOUT_SECONDS = 180.0
BROWSER_MONITOR_INTERVAL_SECONDS = 1.0

SEARCH_MODES = (
    "auto",
    "legacy_auto",
    "strict",
    "balanced",
    "discovery",
    "article_focus",
)

EXCLUDE_MODELS = ("ruri", "embed")


class BrowserSessionMonitor:
    """Track browser presence without treating a normal page reload as shutdown."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_heartbeat: float | None = None
        self._close_deadline: float | None = None

    def heartbeat(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._last_heartbeat = now
            # A newly loaded/reloaded page cancels a pending close request.
            self._close_deadline = None

    def browser_closing(self) -> None:
        with self._lock:
            self._close_deadline = (
                time.monotonic() + BROWSER_CLOSE_GRACE_SECONDS
            )

    def shutdown_reason(self) -> str | None:
        now = time.monotonic()
        with self._lock:
            if (
                self._close_deadline is not None
                and now >= self._close_deadline
            ):
                return "browser page closed"

            if (
                self._last_heartbeat is not None
                and now - self._last_heartbeat
                >= BROWSER_HEARTBEAT_TIMEOUT_SECONDS
            ):
                return "browser heartbeat timed out"

        return None

    def wait(self, timeout: float) -> bool:
        return self._stop_event.wait(timeout)

    def stop(self) -> None:
        self._stop_event.set()


def monitor_browser_session(
    server: ThreadingHTTPServer,
    monitor: BrowserSessionMonitor,
) -> None:
    """Shut the HTTP server down after the browser session disappears."""
    while not monitor.wait(BROWSER_MONITOR_INTERVAL_SECONDS):
        reason = monitor.shutdown_reason()
        if reason is None:
            continue

        print(f"Browser session ended ({reason}). Shutting down.")
        server.shutdown()
        return


@dataclass(frozen=True, slots=True)
class PromptSet:
    system: str
    user_template: Template
    mode: str


@dataclass(frozen=True, slots=True)
class ContextPackage:
    text: str
    titles: list[str]
    char_count: int
    primary_title: str | None


class PromptRepository:
    """Load mode-specific prompts from disk for every request."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _read_required(path: Path) -> str:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise RuntimeError(f"Prompt file not found: {path}") from exc
        except OSError as exc:
            raise RuntimeError(f"Failed to read prompt file: {path}: {exc}") from exc

        if not text:
            raise RuntimeError(f"Prompt file is empty: {path}")
        return text

    def load(self, mode: str) -> PromptSet:
        if mode not in SEARCH_MODES:
            raise ValueError(f"Unsupported search mode: {mode}")

        mode_dir = self.root / mode
        system_text = self._read_required(mode_dir / "system.txt")
        user_text = self._read_required(mode_dir / "user.txt")
        return PromptSet(system=system_text, user_template=Template(user_text), mode=mode)


class ContextBuilder:
    """Build a bounded context without cutting chunks in the middle."""

    @staticmethod
    def _chunk_block(label: str, title: str, chunks: Iterable[Any]) -> str:
        ordered = sorted(chunks, key=lambda item: item.chunk_no)
        body = "\n\n".join(
            f"[chunk {item.chunk_no + 1}/{item.chunk_count}]\n{item.text}"
            for item in ordered
        )
        return f"=== {label} ===\nTitle: {title}\nHandling: {label}\n\n{body}"

    @classmethod
    def build(cls, results: list[Any], max_chars: int) -> ContextPackage:
        if not results:
            return ContextPackage("", [], 0, None)

        grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for result in results:
            grouped[(result.title, result.url)].append(result)

        groups = sorted(
            grouped.items(),
            key=lambda item: max(result.score for result in item[1]),
            reverse=True,
        )
        primary_key = groups[0][0]
        primary_title = primary_key[0]
        primary_budget = max_chars if len(groups) == 1 else int(max_chars * 0.75)
        support_budget = max_chars - primary_budget

        parts: list[str] = []
        titles: list[str] = []
        used = 0

        def append_group(label: str, key: tuple[str, str], chunks: list[Any], budget: int) -> None:
            nonlocal used
            if budget <= 0 or used >= max_chars:
                return

            title, _url = key
            header = f"=== {label} ===\nTitle: {title}\nHandling: {label}\n\n"
            remaining = min(budget, max_chars - used)
            if len(header) >= remaining:
                return

            selected: list[str] = []
            current = len(header)
            for item in sorted(chunks, key=lambda value: value.chunk_no):
                chunk_text = f"[chunk {item.chunk_no + 1}/{item.chunk_count}]\n{item.text}"
                separator = 2 if selected else 0
                if current + separator + len(chunk_text) > remaining:
                    break
                selected.append(chunk_text)
                current += separator + len(chunk_text)

            if not selected:
                return

            block = header + "\n\n".join(selected)
            parts.append(block)
            titles.append(title)
            used += len(block)

        append_group("Primary Article", primary_key, grouped[primary_key], primary_budget)

        support_groups = groups[1:]
        if support_groups and support_budget > 0:
            per_group = max(300, support_budget // len(support_groups))
            for key, chunks in support_groups:
                append_group(
                    "Supplementary Material (May not be identical to question target)",
                    key,
                    chunks,
                    per_group,
                )
                if used >= max_chars:
                    break

        context = "\n\n------------------------------\n\n".join(parts)
        return ContextPackage(
            text=context,
            titles=list(dict.fromkeys(titles)),
            char_count=len(context),
            primary_title=primary_title,
        )



class RagApplication:
    def __init__(
        self,
        *,
        index_dir: Path,
        prompt_dir: Path,
        default_model: str,
        context_chars: int,
        article_viewer: ArticleViewer,
    ) -> None:
        self.index_dir = index_dir
        self.default_model = default_model
        self.context_chars = context_chars
        self.article_viewer = article_viewer
        self.search_engine = SearchEngine(index_dir)
        self.prompt_repository = PromptRepository(prompt_dir)

    @property
    def viewer_name(self) -> str:
        return self.article_viewer.name

    def open_article(self, title: str) -> None:
        self.article_viewer.open(title)

    def close(self) -> None:
        self.article_viewer.close()

    def list_models(self) -> list[str]:
        response = ollama.list()
        raw_models = (
            response.get("models", [])
            if hasattr(response, "get")
            else getattr(response, "models", [])
        )

        names: list[str] = []
        for item in raw_models:
            if hasattr(item, "get"):
                name = item.get("model") or item.get("name")
            else:
                name = getattr(item, "model", None) or getattr(item, "name", None)
            if not name:
                continue

            text = str(name)
            if any(excluded in text.casefold() for excluded in EXCLUDE_MODELS):
                continue
            names.append(text)

        return sorted(set(names), key=str.casefold)

    @staticmethod
    def _validate_request(
        question: str,
        top_k: int,
        search_mode: str,
        think_mode: str,
        context_chars: int,
        num_ctx: int,
        num_predict: int,
        temperature: float,
    ) -> None:
        if not question:
            raise ValueError("Question is empty.")
        if not 1 <= top_k <= 40:
            raise ValueError("top_k must be between 1 and 40.")
        if search_mode not in SEARCH_MODES:
            raise ValueError("Invalid search mode.")
        if think_mode not in {"auto", "true", "false"}:
            raise ValueError("Invalid thinking mode.")
        if not 1_000 <= context_chars <= 100_000:
            raise ValueError("context_chars must be between 1,000 and 100,000.")
        if not 1_024 <= num_ctx <= 131_072:
            raise ValueError("num_ctx must be between 1,024 and 131,072.")
        if not 64 <= num_predict <= 32_768:
            raise ValueError("num_predict must be between 64 and 32,768.")
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2.")

    def ask(
        self,
        *,
        question: str,
        model: str,
        top_k: int,
        search_mode: str,
        think_mode: str,
        context_chars: int,
        num_ctx: int,
        num_predict: int,
        temperature: float,
    ) -> dict[str, Any]:
        total_started = time.perf_counter()
        question = question.strip()
        model = model.strip() or self.default_model

        self._validate_request(
            question,
            top_k,
            search_mode,
            think_mode,
            context_chars,
            num_ctx,
            num_predict,
            temperature,
        )

        search_started = time.perf_counter()
        results = self.search_engine.search(question, top_k=top_k, mode=search_mode)
        search_seconds = time.perf_counter() - search_started

        if not results:
            if search_mode == "strict":
                raise RuntimeError("No matching article titles found in strict mode.")
            raise RuntimeError("No relevant Wikipedia articles found.")

        context = ContextBuilder.build(results, context_chars)
        prompts = self.prompt_repository.load(search_mode)
        user_prompt = prompts.user_template.substitute(
            search_mode=search_mode,
            primary_title=context.primary_title or "未指定",
            context=context.text,
            question=question,
        )

        chat_args: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompts.system},
                {"role": "user", "content": user_prompt},
            ],
            "options": {
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "temperature": temperature,
            },
        }
        if think_mode != "auto":
            chat_args["think"] = think_mode == "true"

        generation_started = time.perf_counter()
        response = ollama.chat(**chat_args)
        generation_seconds = time.perf_counter() - generation_started

        message = response["message"]
        answer = message["content"]
        thinking_text = (
            message.get("thinking")
            if hasattr(message, "get")
            else getattr(message, "thinking", None)
        )

        def response_field(name: str) -> Any:
            return response.get(name) if hasattr(response, "get") else getattr(response, name, None)

        eval_count = response_field("eval_count")
        eval_duration = response_field("eval_duration")
        tokens_per_second = (
            float(eval_count) / (float(eval_duration) / 1_000_000_000)
            if eval_count is not None and eval_duration
            else None
        )

        return {
            "answer": answer,
            "metrics": {
                "model": model,
                "search_mode": search_mode,
                "prompt_mode": prompts.mode,
                "think": think_mode,
                "search_seconds": search_seconds,
                "generation_seconds": generation_seconds,
                "total_seconds": time.perf_counter() - total_started,
                "context_chars_used": context.char_count,
                "result_count": len(results),
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "temperature": temperature,
                "prompt_eval_count": response_field("prompt_eval_count"),
                "eval_count": eval_count,
                "eval_tokens_per_second": tokens_per_second,
                "thinking_chars": len(thinking_text) if thinking_text else 0,
                "done_reason": response_field("done_reason"),
            },
            "results": [
                {
                    "chunk_id": result.chunk_id,
                    "score": result.score,
                    "title": result.title,
                    "url": result.url,
                    "section": result.section,
                    "chunk_no": result.chunk_no,
                    "chunk_count": result.chunk_count,
                    "text": result.text,
                    "page_type": result.page_type,
                    "match_source": result.match_source,
                }
                for result in results
            ],
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "WikipediaRAG/3.0"

    @property
    def app(self) -> RagApplication:
        return self.server.app  # type: ignore[attr-defined]

    @property
    def browser_monitor(self) -> BrowserSessionMonitor:
        return self.server.browser_monitor  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_file(DEFAULT_TEMPLATE_DIR / "index.html", "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            filename = path.removeprefix("/static/")
            file_path = DEFAULT_STATIC_DIR / filename
            content_type, _encoding = mimetypes.guess_type(file_path.name)
            self._send_file(file_path, content_type or "application/octet-stream")
        elif path == "/api/config":
            self.send_json(
                {
                    "default_top_k": DEFAULT_TOP_K,
                    "default_context_chars": self.app.context_chars,
                    "default_num_ctx": DEFAULT_CONTEXT_LENGTH,
                    "default_num_predict": DEFAULT_NUM_PREDICT,
                    "default_temperature": DEFAULT_TEMPERATURE,
                    "default_search_mode": "article_focus",
                    "default_think_mode": DEFAULT_THINK_MODE,
                    "viewer": self.app.viewer_name,
                }
            )
        elif path == "/api/health":
            self.send_json(
                {
                    "ok": True,
                    "index": str(self.app.index_dir),
                    "model": self.app.default_model,
                    "viewer": self.app.viewer_name,
                }
            )
        elif path == "/api/models":
            self._handle_models()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            request = self._read_json_request()
            path = urlparse(self.path).path
            if path == "/api/ask":
                self.send_json(self._handle_ask(request))
            elif path == "/api/open_article":
                self._handle_open_article(request)
            elif path == "/api/heartbeat":
                self.browser_monitor.heartbeat()
                self.send_json({"ok": True})
            elif path == "/api/browser_closing":
                self.browser_monitor.browser_closing()
                self.send_json({"ok": True})
            elif path == "/api/shutdown":
                self.send_json({"ok": True})
                threading.Thread(
                    target=self.server.shutdown,
                    name="rag-shutdown",
                    daemon=True,
                ).start()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, FileNotFoundError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            traceback.print_exc()
            self.send_json(
                {"error": f"{type(exc).__name__}: {exc}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_models(self) -> None:
        try:
            self.send_json(
                {
                    "models": self.app.list_models(),
                    "default_model": self.app.default_model,
                }
            )
        except Exception as exc:
            self.send_json(
                {"error": f"{type(exc).__name__}: {exc}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_ask(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.app.ask(
            question=str(request.get("question", "")),
            model=str(request.get("model", self.app.default_model)),
            top_k=int(request.get("top_k", DEFAULT_TOP_K)),
            search_mode=str(request.get("search_mode", "article_focus")),
            think_mode=str(request.get("think", DEFAULT_THINK_MODE)),
            context_chars=int(request.get("context_chars", self.app.context_chars)),
            num_ctx=int(request.get("num_ctx", DEFAULT_CONTEXT_LENGTH)),
            num_predict=int(request.get("num_predict", DEFAULT_NUM_PREDICT)),
            temperature=float(request.get("temperature", DEFAULT_TEMPERATURE)),
        )

    def _handle_open_article(self, request: dict[str, Any]) -> None:
        title = str(request.get("title", "")).strip()
        if not title:
            raise ValueError("Article title is empty.")
        self.app.open_article(title)
        self.send_json({"ok": True, "title": title, "viewer": self.app.viewer_name})

    def _read_json_request(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Invalid request size.")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object.")
        return payload

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._send_bytes(body, content_type, HTTPStatus.OK)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stdout.write(f"{self.address_string()} - {format % args}\n")

    def send_json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_bytes(self, body: bytes, content_type: str, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Wikipedia RAG Web UI")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--context-chars", type=int, default=DEFAULT_CONTEXT_CHARS)
    parser.add_argument(
        "--viewer",
        choices=("jsonl", "kiwix"),
        default="jsonl",
        help="Article viewer backend (default: jsonl)",
    )
    parser.add_argument(
        "--jsonl-viewer-dir",
        type=Path,
        default=DEFAULT_JSONL_VIEWER_DIR,
        help="Directory containing the bundled Wikipedia JSONL viewer",
    )
    parser.add_argument(
        "--kiwix-url",
        default="http://127.0.0.1:8080",
        help="Kiwix Server URL",
    )
    parser.add_argument(
        "--kiwix-zim",
        default="wikipedia_ja_all",
        help="ZIM name exposed by Kiwix Server",
    )
    parser.add_argument(
        "--kiwix-executable",
        type=Path,
        help="Path to kiwix-serve.exe (required for --viewer kiwix)",
    )
    parser.add_argument(
        "--kiwix-zim-file",
        type=Path,
        help="Path to the Wikipedia ZIM file (required for --viewer kiwix)",
    )
    parser.add_argument(
        "--open-browser",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Open the RAG UI in the default browser",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    index_dir = args.index.resolve()
    prompt_dir = args.prompts.resolve()

    if not index_dir.is_dir():
        print(f"Index directory not found: {index_dir}", file=sys.stderr)
        return 1
    if not prompt_dir.is_dir():
        print(f"Prompt directory not found: {prompt_dir}", file=sys.stderr)
        return 1

    try:
        article_viewer = create_article_viewer(
            viewer_name=args.viewer,
            base_dir=BASE_DIR,
            jsonl_tool_dir=args.jsonl_viewer_dir,
            kiwix_url=args.kiwix_url,
            kiwix_zim=args.kiwix_zim,
            kiwix_executable=args.kiwix_executable,
            kiwix_zim_file=args.kiwix_zim_file,
        )
    except Exception as exc:
        print(f"Failed to initialize article viewer: {exc}", file=sys.stderr)
        return 1

    app = RagApplication(
        index_dir=index_dir,
        prompt_dir=prompt_dir,
        default_model=args.model,
        context_chars=args.context_chars,
        article_viewer=article_viewer,
    )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app  # type: ignore[attr-defined]

    browser_monitor = BrowserSessionMonitor()
    server.browser_monitor = browser_monitor  # type: ignore[attr-defined]
    threading.Thread(
        target=monitor_browser_session,
        args=(server, browser_monitor),
        name="browser-session-monitor",
        daemon=True,
    ).start()

    print("=" * 68)
    print("Wikipedia RAG Web UI")
    print(f"URL:     http://{args.host}:{args.port}")
    print(f"Index:   {index_dir}")
    print(f"Prompts: {prompt_dir}")
    print(f"Model:   {args.model}")
    print(f"Viewer:  {app.viewer_name}")
    if app.viewer_name == "jsonl":
        print(f"Tools:   {args.jsonl_viewer_dir.resolve()}")
    print("Quit:    Ctrl+C")
    print("=" * 68)

    if args.open_browser:
        threading.Timer(
            0.4,
            lambda: webbrowser.open(f"http://{args.host}:{args.port}", new=2),
        ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        browser_monitor.stop()
        server.server_close()
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
