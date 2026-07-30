from __future__ import annotations
import json, logging, re, unicodedata
from pathlib import Path

def configure_logging(path: Path) -> logging.Logger:
    logger = logging.getLogger('rag')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    sh = logging.StreamHandler(); sh.setFormatter(fmt)
    fh = logging.FileHandler(path, encoding='utf-8'); fh.setFormatter(fmt)
    logger.addHandler(sh); logger.addHandler(fh)
    return logger

def read_json(path: Path):
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)

def write_json(path: Path, value: object) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
    tmp.replace(path)

_space_re = re.compile(r'\s+')
def normalize_title(value: str) -> str:
    return _space_re.sub(' ', unicodedata.normalize('NFKC', value).casefold().strip())
