# タイトル検索 3段階一致 更新

## 対象

現在使用中の `raglib/search_engine.py` を更新する。
`webui.py`、FAISS、`metadata.sqlite` の変更・再構築は不要。

## 導入

既存版を保存する。

```powershell
copy raglib\search_engine.py raglib\search_engine_before_title_3stage.py
```

ZIP内の `raglib/search_engine.py` を同じ場所へ上書きし、Web UIを再起動する。

## 判定順

### 1. 通常正規化一致

NFKC、大文字小文字、連続空白を正規化した完全一致。

### 2. 英字・数字境界一致

英字と数字の境界にある空白だけを無視する。

- `Fallout 3` = `fallout3`
- `Windows 11` = `Windows11`

この段階はSQLiteの既存 `normalized_title` インデックスを利用する。

### 3. 一意なcompact一致

空白・中黒・ハイフン・句読点などを除去して比較する。

安全条件：

- compact化後5文字以上
- 一致する記事が一つだけ
- 候補取得は既存 `title_fts` で制限

一致が複数または候補が得られない場合は、記事を確定せず従来検索へフォールバックする。

## 確認例

```text
Fallout 3
fallout3
Ｆａｌｌｏｕｔ　３
```

いずれも `Fallout 3` の記事内検索へ入ることを確認する。

比較用として、曖昧・短いタイトルも試し、誤った単一記事への固定が起きないことを確認する。
