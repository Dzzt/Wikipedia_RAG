webui.py                 Web server and RAG request orchestration
wikirag/                 Runtime Python package
  search_engine.py       Search and ranking
  embedding.py           Ollama embedding client used at runtime and build time
  models.py              Shared data models and build configuration
  utils.py               Shared utilities
  article_viewers.py     JSONL and optional Kiwix article viewers
build/                   Offline index construction code
  build_production.py    Production index builder
  chunker.py             Article chunk construction
  faiss_helpers.py       FAISS index construction helpers
  sqlite_store.py        Build-time SQLite writer
prompts/                 Search-mode-specific prompts
templates/               HTML structure
static/                  CSS and browser-side JavaScript
tools/                   External/local viewer tools

__pycache__ directories are generated automatically by Python and are not
source files. They should not be copied or version-controlled.
