from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
import unicodedata

import faiss
import numpy as np

from .embedding import OllamaEmbedder
from .models import BuildConfig
from .utils import normalize_title, read_json


EXACT_TITLE_BOOST = 0.30
COMPACT_TITLE_BOOST = 0.28
PARTIAL_TITLE_BOOST = 0.10
INTENT_SECTION_BOOST = 0.05
DISAMBIGUATION_PENALTY = -0.08

# These are deliberately modest. Vector similarity remains the main ranking signal.
STORY_QUERY_TERMS = (
    "ストーリー",
    "物語",
    "あらすじ",
    "シナリオ",
    "世界観",
    "設定",
    "プロット",
)
STORY_SECTION_TERMS = (
    "ストーリー",
    "物語",
    "あらすじ",
    "シナリオ",
    "プロット",
)


@dataclass(slots=True)
class Result:
    chunk_id: int
    score: float
    vector_score: float
    title_boost: float
    quality_adjustment: float
    title: str
    url: str
    section: str
    chunk_no: int
    chunk_count: int
    text: str
    page_type: str
    match_source: str


def compact_title(value: str) -> str:
    """Comparison key that ignores width, case, whitespace, punctuation and symbols."""
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(ch for ch in value if ch.isalnum())


def fts_phrase(value: str) -> str:
    """Quote a value safely for an FTS5 phrase query."""
    return '"' + value.replace('"', '""') + '"'


class SearchEngine:
    def __init__(self, index_dir: Path) -> None:
        self.index_dir = Path(index_dir)
        configuration = read_json(self.index_dir / "config.json")
        self.build_config = BuildConfig(**configuration["build"])
        self.index_config = configuration["index"]
        self.embedder = OllamaEmbedder(self.build_config)

        self.connection = sqlite3.connect(
            self.index_dir / "metadata.sqlite",
            check_same_thread=False,
        )

        self.shards: list[tuple[dict, object, np.memmap | None]] = []
        self.shards_by_number: dict[
            int,
            tuple[dict, object, np.memmap | None],
        ] = {}

        for manifest_path in sorted(self.index_dir.glob("shard_*.json")):
            manifest = read_json(manifest_path)
            index = faiss.read_index(
                str(self.index_dir / manifest["faiss_file"])
            )
            index.nprobe = int(self.index_config["nprobe"])

            f16 = None
            if manifest.get("float16_file"):
                f16 = np.memmap(
                    self.index_dir / manifest["float16_file"],
                    mode="r",
                    dtype=np.float16,
                    shape=(
                        int(manifest["chunk_count"]),
                        self.build_config.embedding_dimension,
                    ),
                )

            entry = (manifest, index, f16)
            self.shards.append(entry)
            self.shards_by_number[int(manifest["shard_no"])] = entry

    def get_combined_article(self, chunk_id: int) -> dict:
        """Return every stored chunk for the article containing ``chunk_id``."""
        target = self.connection.execute(
            "SELECT article_id, title, COALESCE(url, '') "
            "FROM chunks WHERE chunk_id=?",
            (chunk_id,),
        ).fetchone()
        if target is None:
            raise LookupError("指定された検索結果が見つかりません。")

        article_id, title, url = target
        rows = self.connection.execute(
            "SELECT chunk_no, chunk_count, text "
            "FROM chunks WHERE article_id=? ORDER BY chunk_no",
            (article_id,),
        ).fetchall()
        if not rows:
            raise LookupError("記事本文のチャンクが見つかりません。")

        return {
            "article_id": article_id,
            "title": str(title),
            "url": str(url),
            "chunk_count": len(rows),
            # The index was built with overlap. It is intentionally retained
            # here so this view represents the exact stored chunks.
            "text": "\n\n".join(str(row[2]) for row in rows),
        }

    def _rerank_vector(
        self,
        query_vector: np.ndarray,
        shard_no: int,
        vector_row: int,
    ) -> float | None:
        shard = self.shards_by_number.get(shard_no)
        if shard is None:
            return None
        f16 = shard[2]
        if f16 is None:
            return None
        return float(
            np.dot(
                np.asarray(f16[vector_row], dtype=np.float32),
                query_vector,
            )
        )

    def _title_matches(
        self,
        normalized_query: str,
    ) -> tuple[dict[int, float], dict[int, set[str]], set[tuple[str, str]]]:
        """
        Return:
          chunk title boosts,
          match-source labels,
          article keys that count as exact/compact title matches.
        """
        boosts: dict[int, float] = {}
        sources: dict[int, set[str]] = {}
        exact_articles: set[tuple[str, str]] = set()

        # Normal exact match first.
        exact_rows = self.connection.execute(
            "SELECT chunk_id, title, COALESCE(url, '') "
            "FROM chunks WHERE normalized_title=? LIMIT 1000",
            (normalized_query,),
        ).fetchall()

        for chunk_id, title, url in exact_rows:
            chunk_id = int(chunk_id)
            boosts[chunk_id] = max(boosts.get(chunk_id, 0.0), EXACT_TITLE_BOOST)
            sources.setdefault(chunk_id, set()).add("title_exact")
            exact_articles.add((str(title), str(url)))

        # If a normal exact title match exists, do not run the broader FTS
        # query. On a multi-million-chunk database that extra query can be
        # disproportionately expensive and is unnecessary.
        query_compact = compact_title(normalized_query)
        if exact_rows:
            return boosts, sources, exact_articles

        # FTS supplies a bounded candidate set only when exact matching failed.
        if len(query_compact) >= 2:
            try:
                fts_rows = self.connection.execute(
                    "SELECT c.chunk_id, c.title, COALESCE(c.url, '') "
                    "FROM title_fts f "
                    "JOIN chunks c ON c.chunk_id=f.chunk_id "
                    "WHERE title_fts MATCH ? "
                    "LIMIT 500",
                    (fts_phrase(normalized_query),),
                ).fetchall()
            except sqlite3.OperationalError:
                fts_rows = []

            # A phrase query can miss spacing variants such as Fallout3/Fallout 3.
            # If so, use the longest alphanumeric fragments as a broader FTS query.
            if not fts_rows:
                fragments = re.findall(r"[0-9A-Za-z]+|[\u3040-\u30ff\u3400-\u9fff]+", normalized_query)
                if fragments:
                    broad_query = " AND ".join(fts_phrase(x) for x in fragments[:6])
                    try:
                        fts_rows = self.connection.execute(
                            "SELECT c.chunk_id, c.title, COALESCE(c.url, '') "
                            "FROM title_fts f "
                            "JOIN chunks c ON c.chunk_id=f.chunk_id "
                            "WHERE title_fts MATCH ? "
                            "LIMIT 500",
                            (broad_query,),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        fts_rows = []

            for chunk_id, title, url in fts_rows:
                chunk_id = int(chunk_id)
                title_text = str(title)
                url_text = str(url)
                title_compact = compact_title(title_text)

                # Exact after removing spaces/punctuation/full-width differences.
                compact_equal = title_compact == query_compact

                # Allow a disambiguator suffix: "Fallout 3 (ゲーム)".
                compact_prefix = (
                    title_compact.startswith(query_compact)
                    and len(title_compact) <= len(query_compact) + 12
                )

                if compact_equal or compact_prefix:
                    boosts[chunk_id] = max(
                        boosts.get(chunk_id, 0.0),
                        COMPACT_TITLE_BOOST,
                    )
                    sources.setdefault(chunk_id, set()).add("title_compact")
                    exact_articles.add((title_text, url_text))
                else:
                    boosts[chunk_id] = max(
                        boosts.get(chunk_id, 0.0),
                        PARTIAL_TITLE_BOOST,
                    )
                    sources.setdefault(chunk_id, set()).add("title_partial")

        return boosts, sources, exact_articles

    def search(
        self,
        query: str,
        top_k: int = 6,
        mode: str = "auto",
    ) -> list[Result]:
        query = query.strip()
        if not query:
            return []
        if mode not in {"auto", "strict", "balanced", "discovery"}:
            raise ValueError(f"Unknown search mode: {mode}")

        query_vector = self.embedder.embed_query(query)
        candidate_count = int(self.index_config["candidate_count"])

        per_shard = max(
            20,
            min(
                candidate_count,
                candidate_count // max(1, len(self.shards)) * 3,
            ),
        )

        vector_scores: dict[int, float] = {}
        match_sources: dict[int, set[str]] = {}

        for _, index, _ in self.shards:
            scores, ids = index.search(
                query_vector.reshape(1, -1),
                per_shard,
            )
            for score, chunk_id in zip(scores[0], ids[0]):
                chunk_id = int(chunk_id)
                if chunk_id < 0:
                    continue
                vector_scores[chunk_id] = max(
                    float(score),
                    vector_scores.get(chunk_id, -1e9),
                )
                match_sources.setdefault(chunk_id, set()).add("vector")

        normalized_query = self._normalize_query_title(query)
        title_boosts, title_sources, exact_articles = self._title_matches(
            normalized_query
        )
        for chunk_id, labels in title_sources.items():
            match_sources.setdefault(chunk_id, set()).update(labels)

        # _title_matches() already returns every chunk of a normal exact-title
        # article through the indexed normalized_title lookup. Re-querying by
        # title+URL would cause a full scan because that pair is not indexed.
        # Compact/FTS matches likewise contribute their matched chunk IDs here.
        article_internal_ids: set[int] = set(title_boosts)

        for chunk_id in article_internal_ids:
            match_sources.setdefault(chunk_id, set()).add("article_internal")

        all_ids = set(vector_scores) | set(title_boosts)
        if not all_ids:
            return []

        id_list = list(all_ids)
        placeholders = ",".join("?" for _ in id_list)
        metadata_rows = self.connection.execute(
            f"SELECT chunk_id, title, COALESCE(url, ''), COALESCE(section, ''), "
            f"chunk_no, chunk_count, text, page_type, quality_weight, "
            f"vector_shard, vector_row "
            f"FROM chunks WHERE chunk_id IN ({placeholders})",
            id_list,
        ).fetchall()

        metadata = {int(row[0]): row for row in metadata_rows}

        use_f16 = bool(
            self.index_config.get("use_float16_rerank", False)
        )
        if use_f16:
            for chunk_id in all_ids:
                row = metadata.get(chunk_id)
                if row is None:
                    continue
                reranked = self._rerank_vector(
                    query_vector,
                    int(row[9]),
                    int(row[10]),
                )
                if reranked is not None:
                    vector_scores[chunk_id] = reranked

        story_intent = any(term in query for term in STORY_QUERY_TERMS)

        scored: list[
            tuple[int, float, float, float, float, str, bool]
        ] = []

        for chunk_id in all_ids:
            row = metadata.get(chunk_id)
            if row is None:
                continue

            vector_score = vector_scores.get(chunk_id, 0.0)
            title_boost = title_boosts.get(chunk_id, 0.0)

            quality_adjustment = (float(row[8]) - 1.0) * 0.25
            if row[7] == "disambiguation":
                quality_adjustment += DISAMBIGUATION_PENALTY

            # Small intent-sensitive bonus within a matched article.
            if story_intent and (str(row[1]), str(row[2])) in exact_articles:
                section_and_head = (
                    str(row[3]) + "\n" + str(row[6])[:240]
                )
                if any(term in section_and_head for term in STORY_SECTION_TERMS):
                    quality_adjustment += INTENT_SECTION_BOOST
                    match_sources.setdefault(chunk_id, set()).add("intent_section")

            final_score = vector_score + title_boost + quality_adjustment
            source = "+".join(
                sorted(match_sources.get(chunk_id, {"unknown"}))
            )
            is_exact_article = (str(row[1]), str(row[2])) in exact_articles

            scored.append(
                (
                    chunk_id,
                    final_score,
                    vector_score,
                    title_boost,
                    quality_adjustment,
                    source,
                    is_exact_article,
                )
            )

        scored.sort(key=lambda item: item[1], reverse=True)

        # Search modes:
        # auto: exact title -> that article only; otherwise semantic search.
        # strict: exact title required.
        # balanced: exact article preferred, related articles may remain.
        # discovery: broad semantic results.
        if mode == "strict" and not exact_articles:
            return []

        if exact_articles and mode in {"auto", "strict"}:
            scored = [item for item in scored if item[6]]

            if story_intent and scored:
                by_chunk_no = {
                    int(metadata[item[0]][4]): item
                    for item in scored
                }
                seed = max(scored, key=lambda item: item[2])
                seed_no = int(metadata[seed[0]][4])
                best_vector = float(seed[2])
                minimum_vector = best_vector - 0.035

                selected = {seed_no: seed}
                left = seed_no - 1
                right = seed_no + 1

                while len(selected) < top_k:
                    choices = []
                    if left in by_chunk_no:
                        choices.append((left, by_chunk_no[left]))
                    if right in by_chunk_no:
                        choices.append((right, by_chunk_no[right]))
                    if not choices:
                        break

                    choices.sort(key=lambda pair: pair[1][2], reverse=True)
                    chosen_no, chosen = choices[0]
                    if float(chosen[2]) < minimum_vector:
                        break

                    selected[chosen_no] = chosen
                    if chosen_no == left:
                        left -= 1
                    else:
                        right += 1

                scored = sorted(
                    selected.values(),
                    key=lambda item: item[1],
                    reverse=True,
                )

        elif exact_articles and mode == "balanced":
            exact_items = [item for item in scored if item[6]]
            other_items = [item for item in scored if not item[6]]
            exact_quota = min(top_k, max(2, (top_k * 2) // 3))
            scored = exact_items[:exact_quota] + other_items


        exact_article_limit = top_k
        ordinary_article_limit = 2

        results: list[Result] = []
        article_counts: dict[tuple[str, str], int] = {}

        for (
            chunk_id,
            final_score,
            vector_score,
            title_boost,
            quality_adjustment,
            source,
            is_exact_article,
        ) in scored:
            row = metadata[chunk_id]
            article_key = (str(row[1]), str(row[2]))
            count = article_counts.get(article_key, 0)
            article_limit = (
                exact_article_limit
                if is_exact_article
                else ordinary_article_limit
            )
            if count >= article_limit:
                continue

            results.append(
                Result(
                    chunk_id=chunk_id,
                    score=final_score,
                    vector_score=vector_score,
                    title_boost=title_boost,
                    quality_adjustment=quality_adjustment,
                    title=str(row[1]),
                    url=str(row[2]),
                    section=str(row[3]),
                    chunk_no=int(row[4]),
                    chunk_count=int(row[5]),
                    text=str(row[6]),
                    page_type=str(row[7]),
                    match_source=source,
                )
            )
            article_counts[article_key] = count + 1

            if len(results) >= top_k:
                break

        return results

    @staticmethod
    def _normalize_query_title(query: str) -> str:
        normalized = normalize_title(query)

        # Remove stacked request/intent suffixes repeatedly.
        # Example:
        #   "ファイナルファンタジーXIV のストーリーについて教えて"
        # becomes:
        #   "ファイナルファンタジーXIV"
        suffixes = (
            "について教えてください",
            "について教えて",
            "について知りたい",
            "について",
            "を教えてください",
            "を教えて",
            "のストーリー",
            "のあらすじ",
            "の物語",
            "のシナリオ",
            "の世界観",
            "とは何ですか",
            "とは",
        )

        changed = True
        while changed and normalized:
            changed = False
            for suffix in suffixes:
                if normalized.endswith(suffix):
                    normalized = normalized[:-len(suffix)].strip()
                    changed = True
                    break

        return normalized
