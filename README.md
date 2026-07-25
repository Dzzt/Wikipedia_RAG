# 日本語Wikipedia RAG — IVFPQ評価・本番構築版

`data\ja_wiki.jsonl`だけを別途配置して使う、独立した新規プロジェクトです。

## 必須環境

- Windows 11
- Python
- Ollama
- `ruri-v3`
- 回答用モデル（例：`qwen3:14b`）
- Pythonパッケージ: `ollama`, `faiss-cpu`, `numpy`, `tqdm`

SQLiteはPython標準ライブラリです。

## 構成

```text
wikipedia_rag_ivfpq/
├─ data/ja_wiki.jsonl
├─ configs/
├─ sample/
├─ benchmarks/
├─ index/
├─ logs/
├─ raglib/
├─ 01_sample_chunks.py
├─ 02_embed_sample.py
├─ 03_benchmark_indexes.py
├─ 04_build_production.py
├─ 05_search.py
└─ 06_chat.py
```

## 0. ruri-embed

既に`ruri-embed`があればそのまま使えます。再作成する場合:

```powershell
ollama create ruri-v3 -f Modelfile.ruri
```

## 1. 評価用50万チャンクを抽出

```powershell
python 01_sample_chunks.py
```

- JSONL全体を一度走査
- SQLiteのReservoir Samplingで偏りを抑えて50万チャンクを抽出
- **追加時間: 約30分～2時間**
- **追加容量: 約0.5～1.5GB**（本文長による）

やり直し:

```powershell
python 01_sample_chunks.py --overwrite
```

## 2. 評価用サンプルをEmbedding

```powershell
python 02_embed_sample.py
```

- Embeddingは一度だけ。後続の全候補で使い回します
- **追加時間: 約4～6時間**
- `sample_vectors.f32`: 50万件なら約1.54GB
- `sample_ids.i64`: 約4MB

## 3. 候補インデックスを比較

```powershell
python 03_benchmark_indexes.py
```

初期候補:

- IVF8192 + PQ64
- IVF16384 + PQ64
- IVF16384 + SQ8

比較:

- Recall
- nprobe 32 / 64 / 128 / 256
- 候補数 100 / 300 / 500
- 平均検索時間
- インデックス容量

- **追加時間: 約2～5時間**
- **追加容量: 数百MB～数GB**

結果:

```text
benchmarks\benchmark_results.json
configs\selected_config.json
```

自動選択結果は必ず確認してください。Recallが低い場合は本番へ進みません。

## 4. 本番構築

```powershell
python 04_build_production.py
```

特徴:

- 選定済みIVFPQ/SQ8へ直接登録
- 巨大な非量子化HNSWを作らない
- metadataはSQLiteへ直接保存
- タイトル完全一致用B-tree
- 対応環境ではFTS5 trigram
- float16再ランキング用データをシャードごとに保存
- 5万記事ごとに完成シャードを確定
- 停止後は完成済みシャードの次から再開

**本番時間は非量子化版より約10～30%増える可能性があります。** 元見積もり60時間なら約66～80時間を見ます。

### ディスク容量

- IVFPQ: 全件で約1～2GBを期待
- float16再ランキング: 700万チャンクなら約10.8GB
- SQLite: 本文・索引で数GB～十数GB

float16再ランキングを省略する場合、`configs\build_config.json`の

```json
"save_float16": false
```

へ変更します。**約10GB前後のディスクを節約**できますが、圧縮検索の順位補正を失います。

## 5. 停止後の再開

同じコマンドを再実行します。

```powershell
python 04_build_production.py
```

完成済みシャードは再利用し、未完成シャードだけをやり直します。

SQLite索引作成だけ再実行:

```powershell
python 04_build_production.py --finalize-only
```

## 6. 検索

```powershell
python 05_search.py "ゲーム・オブ・スローンズについて"
```

## 7. RAG回答

```powershell
python 06_chat.py --model qwen3:14b
```

一回だけ:

```powershell
python 06_chat.py "ナポレオンについて教えて"
```

## 運用時メモリ目標

PQ64採用時のRAG検索側目標:

- FAISSシャード合計: 約1～2GB
- SQLite＋OSキャッシュ: 約0.2～1GB
- Python・FAISS: 約0.5～1GB
- float16 memmap実メモリ: 数十～数百MB

合計約2～4GBを目標とします。

## 判断基準

- 候補Recall 0.95以上が望ましい
- 検索時間1秒未満
- 合格構成が出たら、改善幅2ポイント未満またはサイズ削減25%未満の追加試験は原則打ち切る
- 手法比較そのものを目的化せず、本番総時間とファイル容量を常にコストとして扱う
