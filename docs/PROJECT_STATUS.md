# PROJECT_STATUS.md

# Wikipedia RAG + External Viewer

## 概要

ローカルWikipediaダンプをRAGとして利用するシステム。

目的は、

* ローカルLLMによるWikipedia検索・回答
* 回答の根拠となった記事を簡単に参照すること

である。

全文表示はRAG側では行わず、専用ビューアへ委譲する構成としている。

---

# システム構成

```
webui.py
    │
    ├─ SearchEngine (FAISS)
    ├─ Ollama
    ├─ Web UI
    │
    └─ POST /api/open_article
            │
            ▼
subprocess.Popen()
            │
            ▼
wikipedia_viewer/
    wikipedia_jsonl_viewer.py
            │
            ▼
wikipedia_articles.sqlite3
```

役割は明確に分離している。

## webui.py

担当

* Web UI
* 質問受付
* FAISS検索
* Ollamaへの問い合わせ
* 参照記事一覧表示
* 外部ビューア起動

担当しないもの

* 記事全文取得
* SQLiteアクセス
* 記事表示UI

---

## wikipedia_jsonl_viewer.py

担当

* SQLite読込
* 記事全文表示
* タイトル検索
* 記事表示UI

将来的な機能追加もこちらへ集約する。

---

# ディレクトリ構成

```
RAG/
│
├── webui.py
├── raglib/
├── index/
│
└── wikipedia_viewer/
    ├── wikipedia_jsonl_viewer.py
    └── wikipedia_articles.sqlite3
```

Viewerは必ず

```
webui.py
    ↓
wikipedia_viewer/
```

という配置を前提としている。

---

# 外部ビューア起動

webui.pyは

```
POST /api/open_article
```

を受ける。

Pythonから

```
subprocess.Popen(...)
```

でViewerを起動する。

引数は

```
--db
--title
```

のみ。

Viewer側でSQLite検索を行う。

webui側ではSQLiteへアクセスしない。

---

# 設計方針

## 1. 責務分離

RAGは検索だけ。

Viewerは表示だけ。

両者は独立させる。

---

## 2. 記事本文を重複保持しない

以前は

```
SearchEngine
    ↓
チャンク結合
    ↓
HTML表示
```

だった。

現在は廃止。

全文はViewerのみが扱う。

---

## 3. 記事指定はタイトルのみ

webuiからViewerへ渡す情報は

```
title
```

のみ。

ViewerがSQLiteから取得する。

---

## 4. subprocessのみで連携

通信プロトコルやREST APIは設けない。

Viewerは単独アプリとして起動する。

---

# 現在の動作

検索結果には参照記事一覧を表示する。

記事名クリックで

```
wikipedia_jsonl_viewer.py
```

が起動する。

Viewerは指定タイトルの記事を表示する。

---

# 採用しなかった案

## WebUI内で全文表示

以前は

```
chunk_id
    ↓
get_combined_article()
```

を使っていた。

これは

* RAG側が記事表示まで担当する
* Viewerとの責務が重複する

ため廃止した。

---

## SQLiteをwebuiから読む

採用しない。

SQLiteはViewer専用とする。

---

# 今後の候補

Viewer側

* 記事内検索
* 履歴
* 戻る・進む
* HTML表示
* Wikitext表示
* セクションジャンプ
* 複数記事表示
* タブ対応

webui側

* 参照記事ボタンの改善
* 複数記事同時オープン
* Viewer起動状態との連携

---

# 注意事項

このプロジェクトでは、

**「動作している版を土台にして最小限だけ変更する」**

ことを基本方針とする。

一から書き直すよりも、

既存コードへ局所的に機能追加する方針を優先する。

これは今回、動作実績のある `webui.py` を基準に修正したことで問題なく移行できた経験に基づく。

---

# この文書の目的

新しいチャットでは、この文書を前提情報として読み込ませた上で改良を進める。

コード全体を毎回説明する代わりに、この文書を最新状態へ更新し続けることを前提とする。

