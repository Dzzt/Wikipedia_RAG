# 付録B. JSONL → ベクトル化（RAG構築）

## 目的

JSONLから検索用インデックスを構築する。

生成AIへ渡す本文ではなく、

「検索するための意味ベクトル」

を作成する工程である。

---

## 検討した内容

当初は、

「JSONLをFAISSへ入れる」

という理解だった。

整理した結果、

実際には

```
JSONL
    ↓
チャンク化
    ↓
Embedding
    ↓
ベクトル
    ↓
FAISS
```

であることを確認した。

---

## Embeddingモデル

使用モデル

```
ruri-v3
```

（名古屋大学）

生成AIではなくEmbedding専用モデルとして利用する。

---

## ruri-v3の役割

ruri-v3は

### インデックス生成時

記事チャンク

↓

Embedding

↓

ベクトル生成

### 検索時

質問文

↓

Embedding

↓

同じベクトル空間へ変換

↓

FAISS検索

の両方で利用する。

---

## FAISS

FAISSには

* ベクトル
* 管理情報

のみ保持する。

Wikipedia本文は保持しない。

本文はJSONL等から取得する。

---

## SearchEngine

SearchEngineは

* 質問Embedding
* FAISS検索
* 元チャンク取得

を担当する。

LLMへ渡す本文は検索後に取得される。

---

## この段階で整理した設計

検索用データと本文は分離する。

```
FAISS
    ↓
チャンクID
    ↓
JSONL本文
```

という構造を維持する。

FAISSへ本文を保存しない設計とする。

---

## 採用理由

Embeddingモデルを変更しても

本文データは再利用できる。

Embeddingだけ再生成すればよい。
