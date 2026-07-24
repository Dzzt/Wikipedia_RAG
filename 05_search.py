#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from raglib.search_engine import SearchEngine


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=Path("index"),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=6,
    )
    args = parser.parse_args()

    engine = SearchEngine(args.index_dir)

    for rank, result in enumerate(
        engine.search(
            args.query,
            top_k=args.top_k,
        ),
        1,
    ):
        print("=" * 100)
        print(
            f"{rank}. "
            f"score={result.score:.4f} "
            f"vector={result.vector_score:.4f} "
            f"title_boost={result.title_boost:.4f} "
            f"quality={result.quality_adjustment:.4f} "
            f"source={result.match_source}"
        )
        print(
            f"title={result.title!r} "
            f"chunk={result.chunk_no + 1}/"
            f"{result.chunk_count}"
        )
        print(result.url)
        print(
            result.text[:500].replace(
                "\n",
                " ",
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
