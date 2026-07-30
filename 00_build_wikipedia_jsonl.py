#!/usr/bin/env python3
"""
00_build_wikipedia_jsonl.py

Wikimedia公式の日本語Wikipedia pages-articles XMLダンプを、
RAG構築用JSONLへ全件変換します。

既定の入力:
  E:\\Wikipedia_RAG\\data\\jawiki-latest-pages-articles.xml.bz2

既定の出力:
  E:\\Wikipedia_RAG\\data\\wikipedia_ja_from_dump.jsonl

必要パッケージ:
  mwparserfromhell

実行:
  py -3.13 E:\\Wikipedia_RAG\\00_build_wikipedia_jsonl.py

仕様:
  - .bz2を展開せずストリーミング処理
  - 標準名前空間（ns=0）の通常記事だけを出力
  - リダイレクトは除外
  - 既存RAG互換の text / meta を出力
  - 成功するまでは .partial ファイルへ出力
  - 成功後に正式なJSONL名へ切り替え
"""

from __future__ import annotations

import argparse
import bz2
import html
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import BinaryIO, Iterator

try:
    import mwparserfromhell
except ImportError:
    print(
        "ERROR: mwparserfromhell が必要です。\n"
        "次を実行してください:\n"
        "  py -3.13 -m pip install mwparserfromhell",
        file=sys.stderr,
    )
    raise SystemExit(2)


DEFAULT_INPUT = Path(
    r"E:\Wikipedia_RAG\data\jawiki-latest-pages-articles.xml.bz2"
)
DEFAULT_OUTPUT = Path(
    r"E:\Wikipedia_RAG\data\wikipedia_ja_from_dump.jsonl"
)
WIKIPEDIA_BASE_URL = "https://ja.wikipedia.org/wiki/"

COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
REF_BLOCK_RE = re.compile(
    r"<ref\b[^>/]*?>.*?</ref\s*>", re.IGNORECASE | re.DOTALL
)
REF_SINGLE_RE = re.compile(r"<ref\b[^>]*/\s*>", re.IGNORECASE)
GALLERY_RE = re.compile(
    r"<gallery\b[^>]*?>.*?</gallery\s*>", re.IGNORECASE | re.DOTALL
)
MATH_RE = re.compile(
    r"<math\b[^>]*?>(.*?)</math\s*>", re.IGNORECASE | re.DOTALL
)
TABLE_RE = re.compile(r"^\{\|.*?^\|\}", re.MULTILINE | re.DOTALL)
HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
MULTI_SPACE_RE = re.compile(r"[ \t\u3000]+")
MULTI_BLANK_RE = re.compile(r"\n{3,}")
LEADING_PARTICLE_RE = re.compile(r"^(?:は|とは|（[^）]{0,80}）は)[、，,\s]")

MEDIA_NAMESPACE_PREFIXES = {
    "file",
    "image",
    "ファイル",
    "画像",
}

# 表示文字をテンプレート引数内に持つ代表的なインラインテンプレートです。
# 保守テンプレート、Infobox、Navboxなどは従来どおり除去します。
FIRST_POSITIONAL_TEMPLATE_NAMES = {
    "読み仮名",
    "読み",
    "ruby",
    "ルビ",
    "仮リンク",
    "ill",
    "ill2",
    "illm",
}
LAST_POSITIONAL_TEMPLATE_NAMES = {
    "lang",
    "nowrap",
    "nobr",
    "small",
    "smaller",
    "larger",
    "強調",
    "em",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="公式Wikipedia XMLダンプをRAG用JSONLへ全件変換します。"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--min-chars",
        type=int,
        default=1,
        help="変換後本文がこの文字数未満の記事を除外する（既定: 1）",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10_000,
        help="このns=0ページ数ごとに進捗表示する（既定: 10000）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存の出力・一時ファイルを削除して最初から作り直す",
    )
    args = parser.parse_args()
    if args.min_chars < 0:
        parser.error("--min-chars は0以上で指定してください")
    if args.progress_every <= 0:
        parser.error("--progress-every は1以上で指定してください")
    return args


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def direct_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent:
        if local_name(child.tag) == name:
            return child
    return None


def child_text(parent: ET.Element | None, name: str, default: str = "") -> str:
    if parent is None:
        return default
    child = direct_child(parent, name)
    if child is None or child.text is None:
        return default
    return child.text


def open_dump(path: Path) -> BinaryIO:
    if path.name.lower().endswith(".bz2"):
        return bz2.open(path, "rb")
    return path.open("rb")


def iter_page_elements(stream: BinaryIO) -> Iterator[ET.Element]:
    context = ET.iterparse(stream, events=("start", "end"))
    root: ET.Element | None = None

    for event, elem in context:
        if root is None and event == "start":
            root = elem
            continue
        if event != "end" or local_name(elem.tag) != "page":
            continue

        yield elem

        elem.clear()
        if root is not None:
            root.clear()


def remove_tables(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = TABLE_RE.sub("\n", text)
    return text


def normalized_template_name(template: object) -> str:
    name = str(template.name).strip().replace("_", " ")
    return MULTI_SPACE_RE.sub(" ", name).casefold()


def positional_template_values(template: object) -> list[str]:
    values: list[str] = []
    for parameter in template.params:
        name = str(parameter.name).strip()
        if name.isdecimal():
            value = str(parameter.value).strip()
            if value:
                values.append(value)
    return values


def replace_inline_templates(code: object) -> None:
    """
    言語名、読み、ルビなど、表示文字を引数に持つ小さなテンプレートだけ
    プレーンテキストへ置換します。それ以外のテンプレートは後で除去します。
    """
    for template in list(code.filter_templates(recursive=True)):
        name = normalized_template_name(template)
        values = positional_template_values(template)
        replacement = ""

        if name in FIRST_POSITIONAL_TEMPLATE_NAMES and values:
            replacement = values[0]
        elif name in LAST_POSITIONAL_TEMPLATE_NAMES and values:
            replacement = values[-1]
        elif name.startswith("lang-") and values:
            replacement = values[-1]

        if replacement:
            try:
                code.replace(template, replacement, recursive=True)
            except ValueError:
                pass


def clean_wikitext(title: str, wikitext: str) -> str:
    text = html.unescape(wikitext or "")
    text = COMMENT_RE.sub("", text)
    text = REF_BLOCK_RE.sub("", text)
    text = REF_SINGLE_RE.sub("", text)
    text = GALLERY_RE.sub("", text)
    text = MATH_RE.sub(lambda match: f" {match.group(1)} ", text)
    text = remove_tables(text)
    text = HTML_BREAK_RE.sub("\n", text)

    code = mwparserfromhell.parse(text)

    for wikilink in list(code.filter_wikilinks(recursive=True)):
        target = str(wikilink.title).strip()
        namespace, separator, _rest = target.partition(":")
        if separator and namespace.casefold() in MEDIA_NAMESPACE_PREFIXES:
            try:
                code.remove(wikilink, recursive=True)
            except ValueError:
                pass

    replace_inline_templates(code)

    for template in list(code.filter_templates(recursive=True)):
        try:
            code.remove(template, recursive=True)
        except ValueError:
            pass

    for tag in list(code.filter_tags(recursive=True)):
        tag_name = str(tag.tag).strip().casefold()
        if tag_name in {
            "ref",
            "references",
            "gallery",
            "imagemap",
            "timeline",
            "score",
            "syntaxhighlight",
        }:
            try:
                code.remove(tag, recursive=True)
            except ValueError:
                pass

    text = code.strip_code(normalize=True, collapse=False)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        line = re.sub(r"^[*#;:]+\s*", "", line)
        line = MULTI_SPACE_RE.sub(" ", line).strip()
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = MULTI_BLANK_RE.sub("\n\n", text).strip()

    # タイトル自体がインラインテンプレート内にあり、除去によって冒頭が
    # 「は、...」になった場合の安全策です。
    if text and LEADING_PARTICLE_RE.match(text):
        text = title + text

    return text


def article_url(title: str) -> str:
    encoded = urllib.parse.quote(title.replace(" ", "_"), safe="()")
    return WIKIPEDIA_BASE_URL + encoded


def extract_page(page_elem: ET.Element) -> dict | None:
    ns_text = child_text(page_elem, "ns", "-1")
    try:
        namespace = int(ns_text)
    except ValueError:
        return None
    if namespace != 0:
        return None

    title = child_text(page_elem, "title").strip()
    page_id = child_text(page_elem, "id").strip()
    redirect = direct_child(page_elem, "redirect")

    revision: ET.Element | None = None
    for child in page_elem:
        if local_name(child.tag) == "revision":
            revision = child

    return {
        "title": title,
        "page_id": page_id,
        "is_redirect": redirect is not None,
        "redirect_target": (
            redirect.attrib.get("title", "") if redirect is not None else None
        ),
        "revision_id": child_text(revision, "id").strip(),
        "timestamp": child_text(revision, "timestamp").strip(),
        "sha1": child_text(revision, "sha1").strip(),
        "wikitext": child_text(revision, "text"),
    }


def output_record(page: dict, cleaned_text: str, dump_name: str) -> dict:
    return {
        "text": cleaned_text,
        "meta": {
            "id": page["page_id"],
            "title": page["title"],
            "url": article_url(page["title"]),
            "revision_id": page["revision_id"],
            "timestamp": page["timestamp"],
            "sha1": page["sha1"],
            "redirect": False,
            "redirect_target": None,
            "source": "wikimedia-pages-articles",
            "source_dump": dump_name,
        },
    }


def main() -> int:
    args = parse_args()
    input_path: Path = args.input
    output_path: Path = args.output
    partial_path = output_path.with_suffix(output_path.suffix + ".partial")
    report_path = output_path.with_suffix(output_path.suffix + ".report.json")

    if not input_path.is_file():
        print(f"ERROR: 入力が見つかりません: {input_path}", file=sys.stderr)
        return 2

    existing = [
        path
        for path in (output_path, partial_path, report_path)
        if path.exists()
    ]
    if existing and not args.overwrite:
        print(
            "ERROR: 既存の出力があります。最初から上書きする場合は"
            " --overwrite を指定してください:",
            file=sys.stderr,
        )
        for path in existing:
            print(f"  {path}", file=sys.stderr)
        return 2
    if args.overwrite:
        for path in existing:
            if path.is_file():
                path.unlink()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    stats = {
        "input": str(input_path.resolve()),
        "output": str(output_path.resolve()),
        "pages_seen": 0,
        "namespace_0_pages": 0,
        "redirects_skipped": 0,
        "articles_written": 0,
        "empty_wikitext_skipped": 0,
        "too_short_skipped": 0,
        "conversion_errors": 0,
        "raw_wikitext_chars": 0,
        "cleaned_text_chars": 0,
        "elapsed_seconds": 0.0,
        "completed": False,
    }

    print(f"入力:      {input_path}")
    print(f"出力:      {output_path}")
    print(f"一時出力:  {partial_path}")
    print("リダイレクトは除外します。")
    print()

    started = time.perf_counter()

    try:
        with open_dump(input_path) as source, partial_path.open(
            "w", encoding="utf-8", newline="\n", buffering=1024 * 1024
        ) as destination:
            for page_elem in iter_page_elements(source):
                stats["pages_seen"] += 1
                page = extract_page(page_elem)
                if page is None:
                    continue

                stats["namespace_0_pages"] += 1
                ns0_count = stats["namespace_0_pages"]

                if ns0_count % args.progress_every == 0:
                    elapsed = time.perf_counter() - started
                    rate = (
                        stats["articles_written"] / elapsed
                        if elapsed > 0
                        else 0.0
                    )
                    print(
                        f"[進捗] ns=0 {ns0_count:,} / "
                        f"記事 {stats['articles_written']:,} / "
                        f"転送 {stats['redirects_skipped']:,} / "
                        f"エラー {stats['conversion_errors']:,} / "
                        f"{elapsed / 60:.1f}分 / {rate:.1f}記事/秒"
                    )

                if page["is_redirect"]:
                    stats["redirects_skipped"] += 1
                    continue
                if not page["wikitext"].strip():
                    stats["empty_wikitext_skipped"] += 1
                    continue

                try:
                    cleaned = clean_wikitext(
                        page["title"], page["wikitext"]
                    )
                except Exception as exc:
                    stats["conversion_errors"] += 1
                    print(
                        f"[変換エラー] id={page['page_id']} "
                        f"title={page['title']!r}: {exc}",
                        file=sys.stderr,
                    )
                    continue

                if len(cleaned) < args.min_chars:
                    stats["too_short_skipped"] += 1
                    continue

                record = output_record(page, cleaned, input_path.name)
                destination.write(
                    json.dumps(
                        record, ensure_ascii=False, separators=(",", ":")
                    )
                    + "\n"
                )

                stats["articles_written"] += 1
                stats["raw_wikitext_chars"] += len(page["wikitext"])
                stats["cleaned_text_chars"] += len(cleaned)

        stats["completed"] = True
        stats["elapsed_seconds"] = round(
            time.perf_counter() - started, 3
        )

        # 正常終了した場合だけ正式な出力名へ変更します。
        partial_path.replace(output_path)

    except KeyboardInterrupt:
        stats["elapsed_seconds"] = round(
            time.perf_counter() - started, 3
        )
        print(
            "\n中断しました。一時ファイルは完成品ではありません:\n"
            f"  {partial_path}",
            file=sys.stderr,
        )
        return 130
    except (OSError, EOFError, ET.ParseError) as exc:
        stats["elapsed_seconds"] = round(
            time.perf_counter() - started, 3
        )
        print(f"ERROR: 処理に失敗しました: {exc}", file=sys.stderr)
        print(f"一時ファイル: {partial_path}", file=sys.stderr)
        return 1
    finally:
        with report_path.open(
            "w", encoding="utf-8", newline="\n"
        ) as report:
            json.dump(stats, report, ensure_ascii=False, indent=2)
            report.write("\n")

    print()
    print("=== 完了 ===")
    print(f"全ページ走査数:       {stats['pages_seen']:,}")
    print(f"ns=0ページ数:         {stats['namespace_0_pages']:,}")
    print(f"通常記事出力数:       {stats['articles_written']:,}")
    print(f"リダイレクト除外数:   {stats['redirects_skipped']:,}")
    print(f"空本文除外数:         {stats['empty_wikitext_skipped']:,}")
    print(f"短文除外数:           {stats['too_short_skipped']:,}")
    print(f"変換エラー数:         {stats['conversion_errors']:,}")
    print(f"変換前文字数:         {stats['raw_wikitext_chars']:,}")
    print(f"変換後文字数:         {stats['cleaned_text_chars']:,}")
    print(f"処理時間:             {stats['elapsed_seconds'] / 60:.1f}分")
    print(f"JSONL:                {output_path.resolve()}")
    print(f"レポート:             {report_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
