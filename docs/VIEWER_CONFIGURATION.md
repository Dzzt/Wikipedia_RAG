Article viewer configuration
============================

Default JSONL viewer:

    python webui.py

Expected files:

    tools\wikipedia_viewer\wikipedia_articles.sqlite3
    tools\wikipedia_viewer\wikipedia_jsonl_viewer.cmd
    tools\wikipedia_viewer\wikipedia_jsonl_viewer.py
    tools\wikipedia_viewer\wikipedia_jsonl_viewer_buildindex.ps1

Use a different JSONL viewer directory:

    python webui.py --jsonl-viewer-dir D:\path\to\wikipedia_viewer

Optional Kiwix viewer:

    python webui.py --viewer kiwix ^
      --kiwix-executable D:\path\to\kiwix-serve.exe ^
      --kiwix-zim-file D:\path\to\wikipedia_ja_all.zim

The Kiwix backend is imported and started only when --viewer kiwix is selected.
