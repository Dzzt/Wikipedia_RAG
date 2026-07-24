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
import time
from urllib.parse import urlparse

import ollama

from raglib.search_engine import SearchEngine

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_INDEX = Path("index")
DEFAULT_MODEL = "qwen3:14b"
DEFAULT_TOP_K = 6
DEFAULT_CONTEXT_CHARS = 5000

SYSTEM_PROMPT = """あなたは日本語Wikipediaの参考資料を根拠に回答するアシスタントです。

必ず守ること:
- 質問に直接関係する資料だけを使ってください。
- 別の人物・作品・組織の記事を、質問対象の情報として統合しないでください。
- 最優先記事が示されている場合、その記事を回答の中心にしてください。
- 資料にない年、契約額、成績、人物名、固有名詞を推測で補完しないでください。
- 資料だけでは答えられない点は「参考資料からは確認できません」と明記してください。
- 周辺情報は理解に必要な範囲だけ短く補足してください。
- URLを生成しないでください。
- 回答末尾には、実際に本文の根拠として使ったWikipedia記事名だけを列挙してください。
- 簡潔かつ具体的な日本語で回答してください。"""

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
.grid { display:grid; grid-template-columns:minmax(300px,1fr) 250px 120px; gap:12px; }
.params { display:grid; grid-template-columns:repeat(6,minmax(120px,1fr)); gap:12px; margin-top:14px; }
.metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:8px; margin-top:14px; }
.metric { border:1px solid var(--border); border-radius:8px; padding:9px 11px; background:var(--source); }
.metric-name { color:var(--muted); font-size:12px; }
.metric-value { margin-top:3px; font-weight:600; word-break:break-word; }
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
        <select id="model"><option value="">モデル一覧を取得中...</option></select>
      </div>
      <div><label for="topk">検索件数</label><input id="topk" type="number" value="6" min="1" max="20"></div>
    </div>
    <div class="params">
      <div><label for="searchMode">検索モード</label>
        <select id="searchMode">
          <option value="auto">自動</option>
          <option value="strict">厳密</option>
          <option value="balanced">バランス</option>
          <option value="discovery">発見重視</option>
        </select>
      </div>
      <div><label for="think">Thinking</label><select id="think"><option value="auto">自動</option><option value="false">無効</option><option value="true">有効</option></select></div>
      <div><label for="contextChars">参考資料文字数</label><input id="contextChars" type="number" value="5000" min="1000" max="100000" step="1000"></div>
      <div><label for="numCtx">num_ctx</label><input id="numCtx" type="number" value="4096" min="1024" max="131072" step="1024"></div>
      <div><label for="numPredict">num_predict</label><input id="numPredict" type="number" value="2048" min="64" max="32768" step="64"></div>
      <div><label for="temperature">temperature</label><input id="temperature" type="number" value="0.2" min="0" max="2" step="0.05"></div>
    </div>
    <div class="actions"><button id="ask">質問する</button><span id="status" class="status"></span></div>
  </section>
  <section id="resultPanel" class="panel hidden">
    <h2>回答</h2><div id="answer" class="answer"></div>
    <div id="metrics" class="metrics"></div>
    <div class="sources"><strong>参照記事</strong><div id="sourceTitles"></div></div>
    <details style="margin-top:18px;"><summary>検索結果の詳細</summary><div id="details"></div></details>
  </section>
  <section id="errorPanel" class="panel hidden"><h2>エラー</h2><div id="error" class="error"></div></section>
</main>
<script>
const askButton=document.getElementById("ask"),question=document.getElementById("question"),model=document.getElementById("model"),topk=document.getElementById("topk"),think=document.getElementById("think"),contextChars=document.getElementById("contextChars"),numCtx=document.getElementById("numCtx"),numPredict=document.getElementById("numPredict"),temperature=document.getElementById("temperature"),status=document.getElementById("status"),resultPanel=document.getElementById("resultPanel"),errorPanel=document.getElementById("errorPanel"),answer=document.getElementById("answer"),sourceTitles=document.getElementById("sourceTitles"),details=document.getElementById("details"),metrics=document.getElementById("metrics"),error=document.getElementById("error");
function metric(name,value){const box=document.createElement("div");box.className="metric";const n=document.createElement("div");n.className="metric-name";n.textContent=name;const v=document.createElement("div");v.className="metric-value";v.textContent=value;box.appendChild(n);box.appendChild(v);return box;}
async function loadModels(){try{const response=await fetch("/api/models",{cache:"no-store"});const data=await response.json();if(!response.ok)throw new Error(data.error||"モデル一覧を取得できませんでした。");model.innerHTML="";for(const name of data.models){const option=document.createElement("option");option.value=name;option.textContent=name;if(name===data.default_model)option.selected=true;model.appendChild(option);}if(!data.models.length){const option=document.createElement("option");option.value="";option.textContent="Ollamaモデルがありません";model.appendChild(option);}}catch(e){model.innerHTML="";const option=document.createElement("option");option.value="";option.textContent="モデル一覧取得失敗";model.appendChild(option);status.textContent=String(e);}}
async function ask(){const q=question.value.trim();if(!q){question.focus();return;}if(!model.value){status.textContent="回答モデルを選択してください。";return;}askButton.disabled=true;status.textContent="検索・生成中...";resultPanel.classList.add("hidden");errorPanel.classList.add("hidden");try{const payload={question:q,model:model.value,top_k:Number(topk.value),search_mode:searchMode.value,think:think.value,context_chars:Number(contextChars.value),num_ctx:Number(numCtx.value),num_predict:Number(numPredict.value),temperature:Number(temperature.value)};const response=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw new Error(data.error||"処理に失敗しました。");answer.textContent=data.answer;sourceTitles.textContent=data.source_titles.join(" / ");details.innerHTML="";metrics.innerHTML="";const m=data.metrics;metrics.appendChild(metric("モデル",m.model));metrics.appendChild(metric("検索モード",m.search_mode));metrics.appendChild(metric("Thinking",m.think));metrics.appendChild(metric("検索時間",`${m.search_seconds.toFixed(3)} 秒`));metrics.appendChild(metric("生成時間",`${m.generation_seconds.toFixed(3)} 秒`));metrics.appendChild(metric("合計時間",`${m.total_seconds.toFixed(3)} 秒`));metrics.appendChild(metric("参考資料",`${m.context_chars_used.toLocaleString()} 文字`));metrics.appendChild(metric("検索結果",`${m.result_count} チャンク`));metrics.appendChild(metric("num_ctx",String(m.num_ctx)));metrics.appendChild(metric("num_predict",String(m.num_predict)));metrics.appendChild(metric("temperature",String(m.temperature)));if(m.prompt_eval_count!=null)metrics.appendChild(metric("入力トークン",String(m.prompt_eval_count)));if(m.eval_count!=null)metrics.appendChild(metric("出力トークン",String(m.eval_count)));if(m.eval_tokens_per_second!=null)metrics.appendChild(metric("生成速度",`${m.eval_tokens_per_second.toFixed(2)} tok/s`));if(m.thinking_chars!=null)metrics.appendChild(metric("Thinking量",`${m.thinking_chars.toLocaleString()} 文字`));if(m.done_reason)metrics.appendChild(metric("終了理由",m.done_reason));for(const item of data.results){const card=document.createElement("details");card.className="source-card";const summary=document.createElement("summary");summary.textContent=`${item.title} — chunk ${item.chunk_no+1}/${item.chunk_count}`;const meta=document.createElement("div");meta.className="meta";meta.textContent=`score=${item.score.toFixed(4)} / source=${item.match_source} / page_type=${item.page_type}`;const excerpt=document.createElement("div");excerpt.className="excerpt";excerpt.textContent=item.text;card.appendChild(summary);card.appendChild(meta);if(item.url){const link=document.createElement("a");link.href=item.url;link.target="_blank";link.rel="noreferrer";link.textContent=item.url;card.appendChild(link);}card.appendChild(excerpt);details.appendChild(card);}resultPanel.classList.remove("hidden");status.textContent="完了";}catch(e){error.textContent=String(e);errorPanel.classList.remove("hidden");status.textContent="失敗";}finally{askButton.disabled=false;}}
askButton.addEventListener("click",ask);question.addEventListener("keydown",e=>{if(e.ctrlKey&&e.key==="Enter")ask();});loadModels();
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
            f"[chunk {x.chunk_no + 1}/{x.chunk_count}]\n{x.text}"
            for x in chunks
        )
        block = (
            f"=== {label} ===\n"
            f"記事名: {title}\n"
            f"扱い: {label}\n\n"
            f"{body}"
        )
        allowed = min(budget, max_chars - used)
        if allowed <= 0:
            return
        block = block[:allowed]
        parts.append(block)
        titles.append(title)
        used += len(block)

    append_group("最優先記事", primary_key, grouped[primary_key], primary_budget)

    support_groups = groups[1:]
    if support_groups and support_budget > 0:
        each = max(300, support_budget // len(support_groups))
        for key, chunks in support_groups:
            if used >= max_chars:
                break
            append_group(
                "補助資料（質問対象と同一とは限らない）",
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
        raw_models = response.get("models", []) if hasattr(response, "get") else getattr(response, "models", [])
        names = []
        for item in raw_models:
            if hasattr(item, "get"):
                name = item.get("model") or item.get("name")
            else:
                name = getattr(item, "model", None) or getattr(item, "name", None)
            if name:
                names.append(str(name))
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
        model = model.strip() or self.default_model

        if not question:
            raise ValueError("質問が空です。")
        if not 1 <= top_k <= 20:
            raise ValueError("検索件数は1～20で指定してください。")
        if search_mode not in {"auto", "strict", "balanced", "discovery"}:
            raise ValueError("検索モードが不正です。")
        if not 1000 <= context_chars <= 100000:
            raise ValueError("参考資料文字数は1,000～100,000で指定してください。")
        if not 1024 <= num_ctx <= 131072:
            raise ValueError("num_ctxは1,024～131,072で指定してください。")
        if not 64 <= num_predict <= 32768:
            raise ValueError("num_predictは64～32,768で指定してください。")
        if not 0 <= temperature <= 2:
            raise ValueError("temperatureは0～2で指定してください。")
        if think_mode not in {"auto", "true", "false"}:
            raise ValueError("Thinking指定が不正です。")

        started = time.perf_counter()
        results = self.search_engine.search(
            question,
            top_k=top_k,
            mode=search_mode,
        )
        search_seconds = time.perf_counter() - started

        if not results:
            if search_mode == "strict":
                raise RuntimeError("厳密検索で一致する記事タイトルが見つかりませんでした。")
            raise RuntimeError("関連するWikipedia記事が見つかりませんでした。")

        context, source_titles, context_chars_used, primary_title = make_context(
            results,
            context_chars,
        )

        prompt = f"""以下の参考資料を使って質問に回答してください。

検索モード: {search_mode}
最優先記事: {primary_title or "特定なし"}

資料の扱い:
1. 最優先記事を回答の中心にしてください。
2. 補助資料は、質問対象と明確に関係する場合だけ使ってください。
3. 別人物・別作品・別組織の情報を質問対象へ混ぜないでください。
4. 資料にない具体的事実を補完しないでください。
5. URLは書かず、実際に使った記事名だけを最後に列挙してください。

【参考資料】
{context}

【質問】
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
        path = urlparse(self.path).path
        if path == "/":
            self.send_text(HTML, "text/html; charset=utf-8")
            return
        if path == "/api/health":
            self.send_json({"ok": True, "index": str(self.app.index_dir), "model": self.app.default_model})
            return
        if path == "/api/models":
            try:
                self.send_json({"models": self.app.list_models(), "default_model": self.app.default_model})
            except Exception as exc:
                self.send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
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
                search_mode=str(request.get("search_mode", "auto")),
                think_mode=str(request.get("think", "auto")),
                context_chars=int(request.get("context_chars", self.app.context_chars)),
                num_ctx=int(request.get("num_ctx", 4096)),
                num_predict=int(request.get("num_predict", 2048)),
                temperature=float(request.get("temperature", 0.2)),
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
