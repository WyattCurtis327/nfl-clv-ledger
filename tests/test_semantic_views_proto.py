"""Optional smoke: semantic_views loads and demos run against sample CSV ledger."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "run_semantic_views_proto.py"
SAMPLE = REPO / "samples" / "week1_sample.csv"


def _seed_tmp_ledger(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        """
        CREATE TABLE ledger (
          id BIGINT PRIMARY KEY,
          season INTEGER NOT NULL,
          week INTEGER NOT NULL,
          game_id VARCHAR NOT NULL,
          side VARCHAR NOT NULL,
          market VARCHAR NOT NULL,
          book VARCHAR,
          open_price DOUBLE,
          bet_price DOUBLE,
          close_price DOUBLE,
          open_ts VARCHAR,
          bet_ts VARCHAR,
          close_ts VARCHAR,
          no_vig_clv_pp DOUBLE,
          result VARCHAR NOT NULL DEFAULT 'pending',
          notes VARCHAR
        )
        """
    )
    # Minimal rows so metrics are non-empty without depending on live NFL_CLV_DB.
    con.execute(
        """
        INSERT INTO ledger
          (id, season, week, game_id, side, market, book, no_vig_clv_pp, result)
        VALUES
          (1, 2026, 1, 'g1', 'A', 'ats', 'draftkings', 0.1, 'win'),
          (2, 2026, 1, 'g2', 'B', 'ats', 'fanduel', -0.2, 'loss'),
          (3, 2026, 1, 'g3', 'C', 'ats', 'betmgm', NULL, 'push')
        """
    )
    con.close()


def test_semantic_views_extension_loads() -> None:
    con = duckdb.connect(":memory:")
    try:
        con.execute("INSTALL semantic_views FROM community")
        con.execute("LOAD semantic_views")
    except Exception as exc:
        pytest.fail(f"semantic_views failed to load on DuckDB {duckdb.__version__}: {exc}")
    finally:
        con.close()


def test_run_script_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "ledger.duckdb"
    _seed_tmp_ledger(db)
    monkeypatch.setenv("NFL_CLV_DB", str(db))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "DuckDB version:" in out
    assert "nfl_ats_process" in out
    assert "(a) season" in out
    assert "(b) by week" in out
    assert "(c) by book" in out
    assert "draftkings" in out
