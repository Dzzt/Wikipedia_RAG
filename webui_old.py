#!/usr/bin/env python3
"""
webui.py

依存追加なしで動作する、完全ローカルのWikipedia RAG Web UI。
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import traceback
from urllib.parse import urlparse

import ollama

from raglib.search_engine import SearchEngine

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_INDEX = Path("index")
DEFAULT_MODEL = "qwen3:14b"
DEFAULT_TOP_K = 6
DEFAULT_CONTEXT_CHARS = 10000

SYSTEM_PROMPT = """あなたは日本語Wikipediaを根拠に回答するアシスタントです。
渡された参考資料を優先し、資料にない事実を断定しないでください。
情報が不足している場合は、その旨を明記してください。
簡潔かつ具体的に回答してください。
回答末尾に、参照したWikipedia記事名を列挙してください。"""

HTML = r'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wikipedia RAG</title>
<style>
:root { color-scheme: light dark; --bg:#f5f6f8; --panel:#fff; --text:#1d232a; --muted:#667085; --border:#d8dee6; --accent:#2f6fed; --answer:#eef4ff; --source:#f8fafc; }
@media (prefers-color-scheme: dark) { :root { --bg:#15181d; --panel:#20242b; --text:#eef2f7; --muted:#a6afbd; --border:#3b424d; --accent:#7aa2ff; --answer:#1d2a43; --source:#191d23; } }
* { box-sizing:border-box; }
body { margin:0; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }
main { max-width:1050px; margin:0 auto; padding:28px 18px 60px; }
h1 { margin:0 0 6px; font-size:28px; }
.subtitle { color:var(--muted); margin-bottom:20px; }
.panel { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:18px; margin-bottom:18px; }
.grid { display:grid; grid-template-columns:1fr 220px 120px; gap:12px; }
label { display:block; font-size:13px; color:var(--muted); margin-bottom:5px; }
textarea,select,input[type=number] { width:100%; border:1px solid var(--border); border-radius:8px; padding:10px; font:inherit; background:transparent; color:inherit; }
textarea { min-height:120px; resize:vertical; }
button { border:0; border-radius:8px; padding:11px 18px; font:inherit; font-weight:600; cursor:pointer; background:var(--accent); color:#fff; }
button:disabled { opacity:.55; cursor:wait; }
.actions { display:flex; align-items:center; gap:14px; margin-top:14px; }
.status { color:var(--muted); font-size:14px; }
.answer { white-space:pre-wrap; line-height:1.75; background:var(--answer); border-radius:10px; padding:18px; }
.sources { margin-top:14px; font-size:14px; }
.source-card { border:1px solid var(--border); background:var(--source); border-radius:9px; padding:12px; margin-top:10px; }
.source-card summary { cursor:pointer; font-weight:600; }
.meta { color:var(--muted); font-size:12px; margin:7px 0; }
.excerpt { white-space:pre-wrap; line-height:1.6; }
.hidden { display:none; }
.error { color:#c0392b; white-space:pre-wrap; }
@media (max-width:760px) { .grid { grid-template-columns:1fr; } }
</style>
</head>
<body>
<main>
  <h1>Wikipedia RAG</h1>
  <div class="subtitle">ruri-embed + FAISS + Ollama。通信はローカルのみです。</div>
  <section class="panel">
    <div class="grid">
      <div><label for="question">質問</label><textarea id="question" placeholder="例：ナポレオンについて教えて"></textarea></div>
      <div><label for="model">回答モデル</label>
        <select id="model">
            <option value="qwen3:14b">qwen3:14b</option>
            <option value="gemma4:12b">gemma4:12b</option>
            <option value="Mistral-Nemo-Japanese">Mistral-Nemo-Japanese</option>
        </select>
      </div>
      <div><label for="topk">検索件数</label><input id="topk" type="number" value="6" min="1" max="20"></div>
    </div>
    <div class="actions"><button id="ask">質問する</button><span id="status" class="status"></span></div>
  </section>
  <section id="resultPanel" class="panel hidden">
    <h2>回答</h2><div id="answer" class="answer"></div>
    <div class="sources"><strong>参照記事</strong><div id="sourceTitles"></div></div>
    <details style="margin-top:18px;"><summary>検索結果の詳細</summary><div id="details"></div></details>
  </section>
  <section id="errorPanel" class="panel hidden"><h2>エラー</h2><div id="error" class="error"></div></section>
</main>
<script>
const askButton=document.getElementById("ask"), question=document.getElementById("question"), model=document.getElementById("model"), topk=document.getElementById("topk"), status=document.getElementById("status"), resultPanel=document.getElementById("resultPanel"), errorPanel=document.getElementById("errorPanel"), answer=document.getElementById("answer"), sourceTitles=document.getElementById("sourceTitles"), details=document.getElementById("details"), error=document.getElementById("error");
async function ask(){
 const q=question.value.trim(); if(!q){question.focus(); return;}
 askButton.disabled=true; status.textContent="検索・生成中..."; resultPanel.classList.add("hidden"); errorPanel.classList.add("hidden");
 try{
  const response=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:q,model:model.value,top_k:Number(topk.value)})});
  const data=await response.json(); if(!response.ok) throw new Error(data.error||"処理に失敗しました。");
  answer.textContent=data.answer; sourceTitles.textContent=data.source_titles.join(" / "); details.innerHTML="";
  for(const item of data.results){
   const card=document.createElement("details"); card.className="source-card";
   const summary=document.createElement("summary"); summary.textContent=`${item.title} — chunk ${item.chunk_no+1}/${item.chunk_count}`;
   const meta=document.createElement("div"); meta.className="meta"; meta.textContent=`score=${item.score.toFixed(4)} / source=${item.match_source} / page_type=${item.page_type}`;
   const excerpt=document.createElement("div"); excerpt.className="excerpt"; excerpt.textContent=item.text;
   card.appendChild(summary); card.appendChild(meta);
   if(item.url){ const link=document.createElement("a"); link.href=item.url; link.target="_blank"; link.rel="noreferrer"; link.textContent=item.url; card.appendChild(link); }
   card.appendChild(excerpt); details.appendChild(card);
  }
  resultPanel.classList.remove("hidden"); status.textContent="完了";
 }catch(e){ error.textContent=String(e); errorPanel.classList.remove("hidden"); status.textContent="失敗"; }
 finally{ askButton.disabled=false; }
}
askButton.addEventListener("click",ask); question.addEventListener("keydown",e=>{ if(e.ctrlKey&&e.key==="Enter") ask(); });
</script>
</body>
</html>'''


def make_context(results, max_chars: int) -> tuple[str, list[str]]:
    grouped: dict[tuple[str, str, str], list] = defaultdict(list)
    for result in results:
        grouped[(result.title, result.url)].append(result)

    groups = sorted(grouped.items(), key=lambda item: max(x.score for x in item[1]), reverse=True)
    parts: list[str] = []
    titles: list[str] = []
    used = 0

    for (title, url), chunks in groups:
        chunks.sort(key=lambda x: x.chunk_no)
        body = "\n\n".join(x.text for x in chunks)
        block = f"【記事】{title}\n【URL】{url}\n{body}"
        if parts and used + len(block) > max_chars:
            continue
        parts.append(block)
        titles.append(title)
        used += len(block)
        if used >= max_chars:
            break

    return "\n\n---\n\n".join(parts), list(dict.fromkeys(titles))


class RagApplication:
    def __init__(self, index_dir: Path, default_model: str, context_chars: int) -> None:
        self.index_dir = index_dir
        self.default_model = default_model
        self.context_chars = context_chars
        self.search_engine = SearchEngine(index_dir)

    def ask(self, question: str, model: str, top_k: int) -> dict:
        question = question.strip()
        model = model.strip() or self.default_model
        if not question:
            raise ValueError("質問が空です。")
        if not 1 <= top_k <= 20:
            raise ValueError("検索件数は1～20で指定してください。")

        results = self.search_engine.search(question, top_k=top_k)
        if not results:
            raise RuntimeError("関連するWikipedia記事が見つかりませんでした。")

        context, source_titles = make_context(results, self.context_chars)
        prompt = f"""以下の参考資料だけを主要な根拠として質問に回答してください。

【参考資料】
{context}

【質問】
{question}
"""
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )

        return {
            "answer": response["message"]["content"],
            "source_titles": source_titles,
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
        path = urlparse(self.path).path
        if path == "/":
            self.send_text(HTML, "text/html; charset=utf-8")
            return
        if path == "/api/health":
            self.send_json({"ok": True, "index": str(self.app.index_dir), "model": self.app.default_model})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/ask":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("不正なリクエストサイズです。")
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            result = self.app.ask(
                question=str(request.get("question", "")),
                model=str(request.get("model", self.app.default_model)),
                top_k=int(request.get("top_k", DEFAULT_TOP_K)),
            )
            self.send_json(result)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

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

    def send_text(self, value: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = value.encode("utf-8")
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
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--context-chars", type=int, default=DEFAULT_CONTEXT_CHARS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    index_dir = args.index.resolve()
    if not index_dir.is_dir():
        print(f"インデックスが見つかりません: {index_dir}", file=sys.stderr)
        print("別の場所にある場合は --index <インデックスフォルダ> を指定してください。", file=sys.stderr)
        return 1

    app = RagApplication(index_dir, args.model, args.context_chars)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app  # type: ignore[attr-defined]

    print("=" * 68)
    print("Wikipedia RAG Web UI")
    print(f"URL:   http://{args.host}:{args.port}")
    print(f"Index: {index_dir}")
    print(f"Model: {args.model}")
    print("終了: Ctrl+C")
    print("=" * 68)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
