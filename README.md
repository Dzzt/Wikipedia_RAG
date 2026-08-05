# Wikipedia RAG

日本語Wikipediaダンプを利用したローカルRAGシステムです。

- Wikipedia XMLダンプからJSONLを生成
- ruri-v3でEmbeddingを生成
- FAISSでベクトル検索
- Ollamaで回答生成
- 参照記事は専用Viewerで全文表示

![WebUI](docs/images/wikipedia_RAG.png)

![Viewer](docs/images/wikipedia_viewer.png)

---

## システム構成

```
Wikipedia XML Dump
        │
        ▼
      JSONL
      │    │
      │    └── SQLite（Viewer）
      │
      └── ruri-v3
              │
              ▼
            FAISS
              │
              ▼
          SearchEngine
              │
              ▼
            Ollama
              │
              ▼
            WebUI
              │
      記事名クリック
              │
              ▼
            Viewer
```

---

## 必要環境

- Python 3.14
- Ollama
- 日本語Wikipediaダンプ（pages-articles XML）
- ruri-v3 GGUF

Embeddingモデル

https://huggingface.co/Targoyle/ruri-v3-310m-GGUF

---

## Pythonライブラリ

```
faiss-cpu
numpy
ollama
tqdm
```

JSONL生成のみ

```
mwparserfromhell
```

---

## 処理の流れ

### 1. WikipediaダンプをJSONLへ変換

```
Wikipedia XML
    ↓
JSONL
```

### 2. JSONLから検索インデックスを生成

```
JSONL
    ↓
チャンク化
    ↓
ruri-v3
    ↓
Embedding
    ↓
FAISS
```

### 3. JSONLから記事ビューア用SQLiteを生成

```
JSONL
    ↓
SQLite
```

### 4. WebUI

```
質問
    ↓
ruri-v3
    ↓
FAISS検索
    ↓
チャンク取得
    ↓
Ollama
    ↓
回答
```

記事名をクリックするとViewerが起動し、SQLiteから記事全文を表示します。

---

## ディレクトリ

```
RAG/
├── webui.py
├── raglib/
├── index/
├── wikipedia_viewer/
└── data/
```

---

## 開発

このプロジェクトは ChatGPT Plus を利用した完全な Vibe Coding により開発しています。

コードの設計・実装・リファクタリング・デバッグは、ChatGPTとの対話を通して行っています。


## 配布物について

このリポジトリは、日本語版WikipediaのダンプデータからローカルRAG環境を
構築するためのソースコードと設定例のみを提供します。

以下のデータやソフトウェアは、このリポジトリでは配布しません。

- Wikipediaのダンプデータ
- Wikipediaから生成したJSONLデータ
- Embeddingベクトル
- FAISSインデックス
- SQLiteメタデータ
- OllamaおよびOllama用モデル
- Embeddingモデル
- Pythonの依存ライブラリ

これらは、利用者が各提供元から直接取得してください。
各データ、モデルおよびソフトウェアには、それぞれの提供元が定める
ライセンスと利用条件が適用されます。

## License

このリポジトリに含まれる自作ソースコードおよび文書は、
Apache License 2.0の下で提供されます。詳細は `LICENSE` を参照してください。

Apache License 2.0は、Wikipediaのコンテンツ、生成されたデータや
インデックス、外部のモデル、ライブラリその他の第三者制作物には
適用されません。