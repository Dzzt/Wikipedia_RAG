#!/usr/bin/env python3
"""
webui.py

A fully local Wikipedia RAG Web UI that runs without additional dependencies.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import ollama
from raglib.search_engine import SearchEngine

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_INDEX = Path("index")

DEFAULT_MODEL = "Mistral-Nemo-Japanese"

DEFAULT_TOP_K = 8
DEFAULT_CONTEXT_CHARS = 10000
DEFAULT_CONTEXT_LENGTH = 8192
DEFAULT_NUM_PREDICT = 3072
DEFAULT_TEMPERATURE = 0.5

# Models to exclude from the selection list (e.g., Embedding models)
EXCLUDE_MODELS = {
    "ruri",
    "embed",
}


BASE_DIR = Path(__file__).resolve().parent
VIEWER_SCRIPT = BASE_DIR / "wikipedia_viewer" / "wikipedia_jsonl_viewer.py"
VIEWER_DB = BASE_DIR / "wikipedia_viewer" / "wikipedia_articles.sqlite3"

SYSTEM_PROMPT = """You are an assistant that answers questions based on Japanese Wikipedia reference materials.

Strict rules to follow:
- Focus your answer on reference materials directly relevant to the question.
- If a primary article is specified, focus your answer on that article.
- Use bullet points when listing items.
- Only supplement peripheral information to the extent necessary for understanding.
- Avoid condensing the information too much. Rely heavily on quotes from the source text and synthesize them into a detailed answer.
- Do not generate URLs.
- Clearly state "Cannot be confirmed from reference materials" for details not present in the sources.
- At the end of your response, list only the Wikipedia article titles that were actually used as evidence in the text.
"""

HTML = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wikipedia RAG</title>
<style>
:root {{ color-scheme: light dark; --bg:#f5f6f8; --panel:#fff; --text:#1d232a; --muted:#667085; --border:#d8dee6; --accent:#2f6fed; --answer:#eef4ff; --source:#f8fafc; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#15181d; --panel:#20242b; --text:#eef2f7; --muted:#a6afbd; --border:#3b424d; --accent:#7aa2ff; --answer:#1d2a43; --source:#191d23; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }}
main {{ max-width:1050px; margin:0 auto; padding:28px 18px 60px; }}
h1 {{ margin:0 0 6px; font-size:25px; }}
.subtitle {{ color:var(--muted); margin-bottom:20px; }}
.panel {{ background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:18px; margin-bottom:18px; font-size:12px; }}
.grid {{ display:grid; grid-template-columns:minmax(300px,1fr) 250px 120px; gap:12px; }}
.params {{ display:grid; grid-template-columns:repeat(6,minmax(120px,1fr)); gap:12px; margin-top:14px; }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:8px; margin-top:14px; }}
.metric {{ border:1px solid var(--border); border-radius:8px; padding:9px 11px; background:var(--source); }}
.metric-name {{ color:var(--muted); font-size:9px; }}
.metric-value {{ margin-top:3px; font-weight:600; word-break:break-word; }}
label {{ display:block; font-size:inherit; color:var(--muted); margin-bottom:5px; }}
textarea,select,input[type=number] {{ width:100%; border:1px solid var(--border); border-radius:8px; padding:10px; font:inherit; background:transparent; color:inherit; }}
textarea {{ min-height:120px; resize:vertical; }}
button {{ border:0; border-radius:8px; padding:11px 18px; font:inherit; font-weight:600; cursor:pointer; background:var(--accent); color:#fff; }}
button:disabled {{ opacity:.55; cursor:wait; }}
.actions {{ display:flex; align-items:center; gap:14px; margin-top:14px; }}
.status {{ color:var(--muted); font-size:inherit; }}
.answer {{ white-space:pre-wrap; line-height:1.75; background:var(--answer); border-radius:10px; padding:18px; font-size:14px; }}
.sources {{ margin-top:14px; font-size:11px; }}
.source-card {{ border:1px solid var(--border); background:var(--source); border-radius:9px; padding:12px; margin-top:10px; }}
.article-row {{ display:flex; align-items:center; justify-content:space-between; gap:12px; }}
.article-button {{ padding:0; background:transparent; color:var(--accent); text-align:left; }}
.article-button:hover {{ text-decoration:underline; }}
.meta {{ color:var(--muted); font-size:9px; margin:7px 0; }}
.excerpt {{ white-space:pre-wrap; line-height:1.6; }}
.hidden {{ display:none; }}
.error {{ color:#c0392b; white-space:pre-wrap; }}
@media (max-width:760px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<main>
  <h1>Wikipedia RAG</h1>
  <div class="subtitle">ruri-v3-m310 + FAISS + Ollama. Local execution only.</div>
  <section class="panel">
    <div class="grid">
      <div><label for="question">Question:</label><textarea id="question" placeholder="Enter prompt..."></textarea></div>
      <div><label for="model">Model:</label>
        <select id="model"><option value="">Fetching model list...</option></select>
      </div>
      <div><label for="topk">Top K</label><input id="topk" type="number" value="{DEFAULT_TOP_K}" min="1" max="40"></div>
    </div>
    <div class="params">
      <div><label for="searchMode">Search mode:</label>
        <select id="searchMode">
          <option value="auto">auto</option>
          <option value="legacy_auto">legacy</option>
          <option value="strict">strict</option>
          <option value="balanced">balance</option>
          <option value="discovery">discovery</option>
          <option value="article_focus" selected>article focus</option>
        </select>
      </div>
      <div><label for="think">Thinking</label><select id="think"><option value="auto">auto</option><option value="false">disable</option><option value="true">enable</option></select></div>
      <div><label for="contextChars">Context Chars</label><input id="contextChars" type="number" value="{DEFAULT_CONTEXT_CHARS}" min="1000" max="100000" step="1000"></div>
      <div><label for="numCtx">num_ctx</label><input id="numCtx" type="number" value="{DEFAULT_CONTEXT_LENGTH}" min="1024" max="131072" step="1024"></div>
      <div><label for="numPredict">num_predict</label><input id="numPredict" type="number" value="{DEFAULT_NUM_PREDICT}" min="64" max="32768" step="64"></div>
      <div><label for="temperature">temperature</label><input id="temperature" type="number" value="{DEFAULT_TEMPERATURE}" min="0" max="2" step="0.05"></div>
    </div>
    <div class="actions"><button id="ask">Ask</button><span id="status" class="status"></span></div>
  </section>
  <section id="resultPanel" class="panel hidden">
    <h2>Answer</h2><div id="answer" class="answer"></div>
    <div id="metrics" class="metrics"></div>
    <div class="sources"><strong>Referenced Articles</strong><div id="sourceTitles"></div></div>
    <details style="margin-top:18px;"><summary>Referenced Articles List</summary><div id="details"></div></details>
  </section>
  <section id="errorPanel" class="panel hidden"><h2>Error</h2><div id="error" class="error"></div></section>
</main>

<script>
const askButton=document.getElementById("ask"),question=document.getElementById("question"),model=document.getElementById("model"),topk=document.getElementById("topk"),searchMode=document.getElementById("searchMode"),think=document.getElementById("think"),contextChars=document.getElementById("contextChars"),numCtx=document.getElementById("numCtx"),numPredict=document.getElementById("numPredict"),temperature=document.getElementById("temperature"),status=document.getElementById("status"),resultPanel=document.getElementById("resultPanel"),errorPanel=document.getElementById("errorPanel"),answer=document.getElementById("answer"),sourceTitles=document.getElementById("sourceTitles"),details=document.getElementById("details"),metrics=document.getElementById("metrics"),error=document.getElementById("error");
function metric(name,value){{const box=document.createElement("div");box.className="metric";const n=document.createElement("div");n.className="metric-name";n.textContent=name;const v=document.createElement("div");v.className="metric-value";v.textContent=value;box.appendChild(n);box.appendChild(v);return box;}}

async function loadModels(){{
  try{{
    const response=await fetch("/api/models",{{cache:"no-store"}});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||"Failed to fetch model list.");
    
    model.innerHTML="";
    const defaultModelNorm = (data.default_model || "").toLowerCase();
    let hasSelected = false;

    for(const name of data.models){{
      const option=document.createElement("option");
      option.value=name;
      option.textContent=name;
      
      const normName = name.toLowerCase();
      if(!hasSelected && (
          normName === defaultModelNorm || 
          normName === `${{defaultModelNorm}}:latest` || 
          normName.startsWith(`${{defaultModelNorm}}:`)
      )){{
        option.selected=true;
        hasSelected=true;
      }}
      model.appendChild(option);
    }}
    
    if(!hasSelected && model.options.length > 0){{
      model.options[0].selected = true;
    }}

    if(!data.models.length){{
      const option=document.createElement("option");
      option.value="";
      option.textContent="No Ollama models available";
      model.appendChild(option);
    }}
  }}catch(e){{
    model.innerHTML="";
    const option=document.createElement("option");
    option.value="";
    option.textContent="Failed to load models";
    model.appendChild(option);
    status.textContent=String(e);
  }}
}}

async function openArticle(title){{try{{const response=await fetch("/api/open_article",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{title}})}});const data=await response.json();if(!response.ok)throw new Error(data.error||"Failed to launch article viewer.");status.textContent=`Launched article viewer: ${{title}}`;}}catch(e){{error.textContent=String(e);errorPanel.classList.remove("hidden");status.textContent="Failed to launch viewer";}}}}
function showArticleLinks(results){{details.innerHTML="";const articles=new Map();for(const item of results){{const key=item.title;if(!articles.has(key))articles.set(key,{{title:item.title,matched:0,chunk_count:item.chunk_count}});articles.get(key).matched++;}}for(const item of articles.values()){{const card=document.createElement("div");card.className="source-card";const row=document.createElement("div");row.className="article-row";const button=document.createElement("button");button.type="button";button.className="article-button";button.textContent=item.title;button.addEventListener("click",()=>openArticle(item.title));const count=document.createElement("span");count.className="meta";count.textContent=`Matched ${{item.matched}} / Total ${{item.chunk_count}} chunks`;row.appendChild(button);row.appendChild(count);card.appendChild(row);details.appendChild(card);}}}}
async function ask(){{const q=question.value.trim();if(!q){{question.focus();return;}}if(!model.value){{status.textContent="Please select a model.";return;}}askButton.disabled=true;status.textContent="Searching & Generating...";resultPanel.classList.add("hidden");errorPanel.classList.add("hidden");try{{const payload={{question:q,model:model.value,top_k:Number(topk.value),search_mode:searchMode.value,think:think.value,context_chars:Number(contextChars.value),num_ctx:Number(numCtx.value),num_predict:Number(numPredict.value),temperature:Number(temperature.value)}};const response=await fetch("/api/ask",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(payload)}});const data=await response.json();if(!response.ok)throw new Error(data.error||"Processing failed.");answer.textContent=data.answer;sourceTitles.textContent=data.source_titles.join(" / ");showArticleLinks(data.results);metrics.innerHTML="";const m=data.metrics;metrics.appendChild(metric("Model",m.model));metrics.appendChild(metric("Search Mode",m.search_mode));metrics.appendChild(metric("Thinking",m.think));metrics.appendChild(metric("Search Time",`${{m.search_seconds.toFixed(3)}} s`));metrics.appendChild(metric("Generation Time",`${{m.generation_seconds.toFixed(3)}} s`));metrics.appendChild(metric("Total Time",`${{m.total_seconds.toFixed(3)}} s`));metrics.appendChild(metric("Context Chars",`${{m.context_chars_used.toLocaleString()}} chars`));metrics.appendChild(metric("Search Results",`${{m.result_count}} chunks`));metrics.appendChild(metric("num_ctx",String(m.num_ctx)));metrics.appendChild(metric("num_predict",String(m.num_predict)));metrics.appendChild(metric("temperature",String(m.temperature)));if(m.prompt_eval_count!=null)metrics.appendChild(metric("Input Tokens",String(m.prompt_eval_count)));if(m.eval_count!=null)metrics.appendChild(metric("Output Tokens",String(m.eval_count)));if(m.eval_tokens_per_second!=null)metrics.appendChild(metric("Speed",`${{m.eval_tokens_per_second.toFixed(2)}} tok/s`));if(m.thinking_chars!=null)metrics.appendChild(metric("Thinking Chars",`${{m.thinking_chars.toLocaleString()}} chars`));if(m.done_reason)metrics.appendChild(metric("Done Reason",m.done_reason));resultPanel.classList.remove("hidden");status.textContent="Completed";}}catch(e){{error.textContent=String(e);errorPanel.classList.remove("hidden");status.textContent="Failed";}}finally{{askButton.disabled=false;}}}}
askButton.addEventListener("click",ask);question.addEventListener("keydown",e=>{{if(e.ctrlKey&&e.key==="Enter")ask();}});loadModels();
</script>
</body>
</html>'''


def make_context(
    results,
    max_chars: int,
) -> tuple[str, list[str], int, str | None]:
    if not results:
        return "", [], 0, None

    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for result in results:
        grouped[(result.title, result.url)].append(result)

    groups = sorted(
        grouped.items(),
        key=lambda item: max(x.score for x in item[1]),
        reverse=True,
    )
    primary_key = groups[0][0]
    primary_title = primary_key[0]

    primary_budget = max_chars if len(groups) == 1 else int(max_chars * 0.75)
    support_budget = max_chars - primary_budget
    parts: list[str] = []
    titles: list[str] = []
    used = 0

    def append_group(label: str, key, chunks, budget: int) -> None:
        nonlocal used
        title, _url = key
        chunks.sort(key=lambda x: x.chunk_no)
        body = "\n\n".join(
            f"[chunk {x.chunk_no + 1}/{x.chunk_count}]\n{x.text}" for x in chunks
        )
        block = f"=== {label} ===\nTitle: {title}\nHandling: {label}\n\n{body}"
        allowed = min(budget, max_chars - used)
        if allowed <= 0:
            return
        block = block[:allowed]
        parts.append(block)
        titles.append(title)
        used += len(block)

    append_group("Primary Article", primary_key, grouped[primary_key], primary_budget)

    support_groups = groups[1:]
    if support_groups and support_budget > 0:
        each = max(300, support_budget // len(support_groups))
        for key, chunks in support_groups:
            if used >= max_chars:
                break
            append_group(
                "Supplementary Material (May not be identical to question target)",
                key,
                chunks,
                each,
            )

    context = "\n\n------------------------------\n\n".join(parts)
    return context, list(dict.fromkeys(titles)), len(context), primary_title


class RagApplication:
    def __init__(self, index_dir: Path, default_model: str, context_chars: int) -> None:
        self.index_dir = index_dir
        self.default_model = default_model
        self.context_chars = context_chars
        self.search_engine = SearchEngine(index_dir)

    def list_models(self) -> list[str]:
        response = ollama.list()
        raw_models = (
            response.get("models", [])
            if hasattr(response, "get")
            else getattr(response, "models", [])
        )
        names = []
        for item in raw_models:
            if hasattr(item, "get"):
                name = item.get("model") or item.get("name")
            else:
                name = getattr(item, "model", None) or getattr(item, "name", None)

            if name:
                name_str = str(name)
                if any(ex.lower() in name_str.lower() for ex in EXCLUDE_MODELS):
                    continue
                names.append(name_str)

        return sorted(set(names), key=str.casefold)

    def ask(
        self,
        question: str,
        model: str,
        top_k: int,
        search_mode: str,
        think_mode: str,
        context_chars: int,
        num_ctx: int,
        num_predict: int,
        temperature: float,
    ) -> dict:
        total_started = time.perf_counter()
        question = question.strip()
        model = (
            model.strip() or self.default_default_model
            if hasattr(self, "default_default_model")
            else self.default_model
        )

        if not question:
            raise ValueError("Question is empty.")
        if not 1 <= top_k <= 40:
            raise ValueError("top_k must be between 1 and 40.")
        if search_mode not in {
            "auto",
            "legacy_auto",
            "strict",
            "balanced",
            "discovery",
            "article_focus",
        }:
            raise ValueError("Invalid search mode.")
        if not 1000 <= context_chars <= 100000:
            raise ValueError("context_chars must be between 1,000 and 100,000.")
        if not 1024 <= num_ctx <= 131072:
            raise ValueError("num_ctx must be between 1,024 and 131,072.")
        if not 64 <= num_predict <= 32768:
            raise ValueError("num_predict must be between 64 and 32,768.")
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2.")
        if think_mode not in {"auto", "true", "false"}:
            raise ValueError("Invalid thinking mode.")

        started = time.perf_counter()
        results = self.search_engine.search(
            question,
            top_k=top_k,
            mode=search_mode,
        )
        search_seconds = time.perf_counter() - started

        if not results:
            if search_mode == "strict":
                raise RuntimeError(
                    "No matching article titles found in strict search."
                )
            raise RuntimeError("No relevant Wikipedia articles found.")

        context, source_titles, context_chars_used, primary_title = make_context(
            results,
            context_chars,
        )

        prompt = f"""Please answer the question using the following reference materials.

Search Mode: {search_mode}
Primary Article: {primary_title or "Unspecified"}

Handling Guidelines:
1. Base your answer primarily on the Primary Article.
2. Use supplementary materials only if they are clearly related to the subject of the question.
3. Do not mix information from different persons, works, or organizations into the target entity.
4. Do not complement with specific facts not found in the material.
5. Do not include URLs; list only the article titles actually used at the end.

【Reference Materials】
{context}

【Question】
{question}
"""

        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "options": {
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "temperature": temperature,
            },
        }
        if think_mode != "auto":
            kwargs["think"] = think_mode == "true"

        started = time.perf_counter()
        response = ollama.chat(**kwargs)
        generation_seconds = time.perf_counter() - started
        total_seconds = time.perf_counter() - total_started

        message = response["message"]
        answer = message["content"]
        thinking_text = (
            message.get("thinking")
            if hasattr(message, "get")
            else getattr(message, "thinking", None)
        )

        def field(name):
            return (
                response.get(name)
                if hasattr(response, "get")
                else getattr(response, name, None)
            )

        eval_count = field("eval_count")
        prompt_eval_count = field("prompt_eval_count")
        eval_duration = field("eval_duration")
        done_reason = field("done_reason")
        tps = (
            float(eval_count) / (float(eval_duration) / 1_000_000_000)
            if eval_count is not None and eval_duration
            else None
        )

        return {
            "answer": answer,
            "source_titles": source_titles,
            "metrics": {
                "model": model,
                "search_mode": search_mode,
                "think": think_mode,
                "search_seconds": search_seconds,
                "generation_seconds": generation_seconds,
                "total_seconds": total_seconds,
                "context_chars_used": context_chars_used,
                "result_count": len(results),
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "temperature": temperature,
                "prompt_eval_count": prompt_eval_count,
                "eval_count": eval_count,
                "eval_tokens_per_second": tps,
                "thinking_chars": len(thinking_text) if thinking_text else 0,
                "done_reason": done_reason,
            },
            "results": [
                {
                    "chunk_id": r.chunk_id,
                    "score": r.score,
                    "title": r.title,
                    "url": r.url,
                    "section": r.section,
                    "chunk_no": r.chunk_no,
                    "chunk_count": r.chunk_count,
                    "text": r.text,
                    "page_type": r.page_type,
                    "match_source": r.match_source,
                }
                for r in results
            ],
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "WikipediaRAG/1.0"

    @property
    def app(self) -> RagApplication:
        return self.server.app  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.send_text(HTML, "text/html; charset=utf-8")
            return
        if path == "/api/health":
            self.send_json(
                {
                    "ok": True,
                    "index": str(self.app.index_dir),
                    "model": self.app.default_model,
                }
            )
            return
        if path == "/api/models":
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
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("Invalid request size.")
            request = json.loads(self.rfile.read(length).decode("utf-8"))

            if path == "/api/ask":
                result = self.app.ask(
                    question=str(request.get("question", "")),
                    model=str(request.get("model", self.app.default_model)),
                    top_k=int(request.get("top_k", DEFAULT_TOP_K)),
                    search_mode=str(request.get("search_mode", "article_focus")),
                    think_mode=str(request.get("think", "auto")),
                    context_chars=int(
                        request.get("context_chars", self.app.context_chars)
                    ),
                    num_ctx=int(request.get("num_ctx", DEFAULT_CONTEXT_LENGTH)),
                    num_predict=int(request.get("num_predict", DEFAULT_NUM_PREDICT)),
                    temperature=float(request.get("temperature", DEFAULT_TEMPERATURE)),
                )
                self.send_json(result)
                return

            if path == "/api/open_article":
                title = str(request.get("title", "")).strip()
                if not title:
                    raise ValueError("Article title is empty.")
                if not VIEWER_SCRIPT.is_file():
                    raise FileNotFoundError(
                        f"Viewer script not found: {VIEWER_SCRIPT}"
                    )
                if not VIEWER_DB.is_file():
                    raise FileNotFoundError(
                        f"Article database not found: {VIEWER_DB}"
                    )

                subprocess.Popen(
                    [
                        "py",
                        str(VIEWER_SCRIPT),
                        "--db",
                        str(VIEWER_DB),
                        "--title",
                        title,
                    ],
                    cwd=str(BASE_DIR),
                )
                self.send_json({"ok": True, "title": title})
                return

            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, FileNotFoundError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            traceback.print_exc()
            self.send_json(
                {"error": f"{type(exc).__name__}: {exc}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args) -> None:
        sys.stdout.write(f"{self.address_string()} - {format % args}\n")

    def send_json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_text(
        self, value: str, content_type: str, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        body = value.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local Wikipedia RAG Web UI (experimental)"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--context-chars", type=int, default=DEFAULT_CONTEXT_CHARS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    index_dir = args.index.resolve()
    if not index_dir.is_dir():
        print(f"Index directory not found: {index_dir}", file=sys.stderr)
        print(
            "If located elsewhere, please specify using --index <index_folder>",
            file=sys.stderr,
        )
        return 1

    app = RagApplication(index_dir, args.model, args.context_chars)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app  # type: ignore[attr-defined]

    print("=" * 68)
    print("Wikipedia RAG Web UI (experimental)")
    print(f"URL:   http://{args.host}:{args.port}")
    print(f"Index: {index_dir}")
    print(f"Model: {args.model}")
    print("Quit: Ctrl+C")
    print("=" * 68)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())