#!/usr/bin/env python3
"""Smoke-run DuckDB semantic_views over the CLV ledger.

Opens NFL_CLV_DB (else ./ledger.duckdb), INSTALL/LOAD semantic_views,
CREATE OR REPLACE SEMANTIC VIEW nfl_ats_process, prints three demo tables.

Fails loudly if the community extension will not load.
Does not drop or rewrite fact rows in `ledger`.

Usage:
  export NFL_CLV_DB=/path/to/ledger.duckdb   # optional
  python scripts/run_semantic_views_proto.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import duckdb
except ImportError as exc:  # pragma: no cover
    print("ERROR: duckdb is required (pip install -e .)", file=sys.stderr)
    raise SystemExit(1) from exc

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = REPO_ROOT / "examples" / "semantic_views_proto.sql"

DDL = """
CREATE OR REPLACE SEMANTIC VIEW nfl_ats_process AS
  TABLES (
    l AS ledger PRIMARY KEY (id)
  )
  DIMENSIONS (
    l.season AS l.season,
    l.week AS l.week,
    l.book AS l.book,
    l.market AS l.market,
    l.side AS l.side,
    l.result AS l.result
  )
  METRICS (
    l.pick_count AS COUNT(*),
    l.mean_no_vig_clv_pp AS AVG(l.no_vig_clv_pp),
    l.ats_wins AS COUNT(*) FILTER (WHERE l.result = 'win'),
    l.ats_losses AS COUNT(*) FILTER (WHERE l.result = 'loss'),
    ats_win_rate AS ats_wins * 1.0 / NULLIF(ats_wins + ats_losses, 0)
  );
"""

DEMOS: list[tuple[str, str]] = [
    (
        "(a) season — mean CLV + pick_count (+ ATS W–L separate)",
        """
        SELECT * FROM semantic_view(
          'nfl_ats_process',
          dimensions := ['season'],
          metrics := ['mean_no_vig_clv_pp', 'pick_count', 'ats_wins', 'ats_losses', 'ats_win_rate']
        )
        """,
    ),
    (
        "(b) by week — mean CLV + pick_count",
        """
        SELECT * FROM semantic_view(
          'nfl_ats_process',
          dimensions := ['week'],
          metrics := ['mean_no_vig_clv_pp', 'pick_count']
        )
        """,
    ),
    (
        "(c) by book — mean CLV",
        """
        SELECT * FROM semantic_view(
          'nfl_ats_process',
          dimensions := ['book'],
          metrics := ['mean_no_vig_clv_pp', 'pick_count']
        )
        ORDER BY book
        """,
    ),
]


def resolve_db() -> Path:
    env = os.environ.get("NFL_CLV_DB")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.cwd() / "ledger.duckdb").resolve()


def load_extension(con: duckdb.DuckDBPyConnection) -> None:
    try:
        con.execute("INSTALL semantic_views FROM community")
        con.execute("LOAD semantic_views")
    except Exception as exc:
        print(
            "ERROR: failed to INSTALL/LOAD community extension `semantic_views`.\n"
            "  Docs: https://duckdb.org/community_extensions/extensions/semantic_views\n"
            f"  DuckDB version: {duckdb.__version__}\n"
            f"  Detail: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


def print_relation(title: str, con: duckdb.DuckDBPyConnection, sql: str) -> None:
    rel = con.execute(sql)
    cols = [d[0] for d in rel.description]
    rows = rel.fetchall()
    print(f"\n=== {title} ===")
    print(" | ".join(cols))
    print("-" * max(48, sum(len(c) + 3 for c in cols)))
    for row in rows:
        cells = []
        for v in row:
            if isinstance(v, float):
                cells.append(f"{v:.6g}")
            else:
                cells.append(str(v))
        print(" | ".join(cells))
    if not rows:
        print("(no rows)")


def main() -> int:
    db = resolve_db()
    if not db.is_file():
        print(
            f"ERROR: ledger database not found: {db}\n"
            "  Set NFL_CLV_DB or create ./ledger.duckdb (nfl-clv init).",
            file=sys.stderr,
        )
        return 1

    print(f"DuckDB version: {duckdb.__version__}")
    print(f"Database:       {db}")
    print(f"SQL example:    {SQL_PATH}")

    # Writable connection required for CREATE SEMANTIC VIEW (queries alone can be read-only).
    con = duckdb.connect(str(db))
    try:
        load_extension(con)
        # Extension pin is the community build for this DuckDB ABI (no separate semver in duckdb_extensions()).
        ext = con.execute(
            "SELECT extension_name, loaded, installed, installed_from, install_path "
            "FROM duckdb_extensions() WHERE extension_name = 'semantic_views'"
        ).fetchone()
        print(f"Extension:      {ext}")

        con.execute(DDL)
        print("Semantic view: nfl_ats_process (CREATE OR REPLACE OK)")

        for title, sql in DEMOS:
            print_relation(title, con, sql)

        print(
            "\nNotes:\n"
            "  - mean_no_vig_clv_pp uses AVG (NULLs skipped); process ≠ ATS W–L.\n"
            "  - ats_win_rate = ats_wins / (ats_wins + ats_losses); excludes push/pending/no_bet.\n"
            "  - Fact table `ledger` rows were not modified."
        )
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
