Wikipedia RAG: 記事内再検索を auto の標準動作にする更新

配置:
  search_engine_default.py -> raglib/search_engine.py
  webui_default.py         -> webui.py

既存ファイルは先にバックアップしてください。
例:
  copy raglib\search_engine.py raglib\search_engine_legacy.py
  copy webui.py webui_legacy.py

新しい検索モード:
  auto
    質問先頭から記事タイトルを完全一致で確定できる場合、残余語で記事内再検索。
    タイトルを確定できない場合は従来の auto へフォールバック。

  legacy_auto
    常に従来の auto 検索を使用。比較・切り戻し用。

  article_focus
    記事内再検索を試し、タイトルを確定できない場合は従来 auto へフォールバック。
    現在の auto と結果は基本的に同じ。動作確認用として残している。

インデックス、metadata.sqlite、FAISSシャードの再作成は不要です。

起動:
  py webui.py

確認例:
  ファイナルファンタジーXIVの漆黒のヴィランズについて教えて
  ロサンゼルス・ドジャースの大谷翔平について教えて

同じ質問を「自動（記事内再検索）」と「従来自動」で比較できます。
