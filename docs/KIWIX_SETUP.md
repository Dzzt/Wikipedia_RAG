# Kiwix連携

この構成では、Wikipedia RAGの起動時に`kiwix-serve`を自動起動します。
検索回答の「Referenced Articles」に表示された記事名をクリックすると、
その記事がKiwix Serverから既定のブラウザの新しいタブで開きます。

開いたKiwixは通常のWikipediaビューワーとして、そのまま検索やリンク移動に利用できます。
ブラウザのKiwixタブを閉じても`kiwix-serve`は停止しません。
Wikipedia RAG画面の「Wikipedia RAGを終了」を押すか、コンソールで`Ctrl+C`を押すと、
Wikipedia RAGと一緒に`kiwix-serve`も停止します。

## 配置例

```text
Wikipedia_RAG/
├─ kiwix/
│  ├─ kiwix-serve.exe
│  └─ （Kiwix Toolsに同梱されたDLL類）
├─ data/
│  └─ wikipedia_ja_all_nopic_2026-06.zim
├─ raglib/
│  └─ article_viewer.py
├─ index/
├─ webui.py
└─ start-rag-kiwix.cmd
```

Kiwix ToolsのDLL類は`kiwix-serve.exe`と同じフォルダに置いてください。

## 設定

`start-rag-kiwix.cmd`先頭の次の3行を、実際の配置とZIMに合わせます。

```bat
set "KIWIX_SERVE=kiwix\kiwix-serve.exe"
set "KIWIX_ZIM=data\wikipedia_ja_all_nopic_2026-06.zim"
set "KIWIX_BOOK=wikipedia_ja_all_nopic"
```

`KIWIX_BOOK`はKiwixがURLで使用するZIM名です。通常はZIMファイル名から
日付と`.zim`を除いた名前です。

設定後は`start-rag-kiwix.cmd`をダブルクリックして起動します。
