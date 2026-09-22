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
YAML = REPO / "metrics" / "nfl_ats_process.yaml"
SAMPLE = REPO / "samples" / "week1_sample.csv"

# Live Week-1 shape (NFL_CLV_DB); asserted only when that env points at a real file.
LIVE_WEEK1_PICKS = 11
LIVE_SEASON_MEAN_CLV = -0.055075
LIVE_ATS_WINS = 4
LIVE_ATS_LOSSES = 7
LIVE_ATS_WIN_RATE = 4 / 11


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


def test_run_script_from_yaml_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert YAML.is_file(), f"missing checked-in YAML: {YAML}"
    db = tmp_path / "ledger.duckdb"
    _seed_tmp_ledger(db)
    monkeypatch.setenv("NFL_CLV_DB", str(db))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--from-yaml"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "FROM YAML OK" in out
    assert "YAML source:" in out
    assert "(a) season" in out
    assert "(b) by week" in out
    assert "(c) by book" in out
    assert "draftkings" in out


def test_yaml_matches_sql_ddl_numbers(tmp_path: Path) -> None:
    """YAML CREATE and SQL CREATE agree within float noise on the same ledger."""
    assert YAML.is_file()
    db = tmp_path / "ledger.duckdb"
    _seed_tmp_ledger(db)
    yaml_text = YAML.read_text(encoding="utf-8")
    tag = "svyaml"
    while f"${tag}$" in yaml_text:
        tag += "x"

    con = duckdb.connect(str(db))
    try:
        con.execute("INSTALL semantic_views FROM community")
        con.execute("LOAD semantic_views")

        sql_ddl = """
        CREATE OR REPLACE SEMANTIC VIEW nfl_ats_sql AS
          TABLES (l AS ledger PRIMARY KEY (id))
          DIMENSIONS (
            l.season AS l.season, l.week AS l.week, l.book AS l.book,
            l.market AS l.market, l.side AS l.side, l.result AS l.result
          )
          METRICS (
            l.pick_count AS COUNT(*),
            l.mean_no_vig_clv_pp AS AVG(l.no_vig_clv_pp),
            l.ats_wins AS COUNT(*) FILTER (WHERE l.result = 'win'),
            l.ats_losses AS COUNT(*) FILTER (WHERE l.result = 'loss'),
            ats_win_rate AS ats_wins * 1.0 / NULLIF(ats_wins + ats_losses, 0)
          );
        """
        con.execute(sql_ddl)
        sql_row = con.execute(
            """
            SELECT * FROM semantic_view(
              'nfl_ats_sql',
              dimensions := ['season'],
              metrics := ['mean_no_vig_clv_pp', 'pick_count', 'ats_wins', 'ats_losses', 'ats_win_rate']
            )
            """
        ).fetchone()

        con.execute(
            f"CREATE OR REPLACE SEMANTIC VIEW nfl_ats_yaml FROM YAML ${tag}${yaml_text}${tag}$"
        )
        yaml_row = con.execute(
            """
            SELECT * FROM semantic_view(
              'nfl_ats_yaml',
              dimensions := ['season'],
              metrics := ['mean_no_vig_clv_pp', 'pick_count', 'ats_wins', 'ats_losses', 'ats_win_rate']
            )
            """
        ).fetchone()
    finally:
        con.close()

    assert sql_row is not None and yaml_row is not None
    # season, mean_clv, pick_count, wins, losses, win_rate
    assert sql_row[0] == yaml_row[0] == 2026
    assert sql_row[2] == yaml_row[2] == 3  # seeded picks
    assert sql_row[3] == yaml_row[3] == 1
    assert sql_row[4] == yaml_row[4] == 1
    assert abs(sql_row[1] - yaml_row[1]) < 1e-12
    assert abs(sql_row[5] - yaml_row[5]) < 1e-12
    # AVG skips NULL → (0.1 + -0.2) / 2 = -0.05
    assert abs(sql_row[1] - (-0.05)) < 1e-12
    assert abs(sql_row[5] - 0.5) < 1e-12


@pytest.mark.skipif(
    not Path(os.environ.get("NFL_CLV_DB", "")).expanduser().is_file(),
    reason="NFL_CLV_DB not set to an existing ledger",
)
def test_yaml_live_week1_shape() -> None:
    """Against live Week-1 ledger: 11 picks; CLV and W–L both present; matches SQL."""
    db = Path(os.environ["NFL_CLV_DB"]).expanduser().resolve()
    yaml_text = YAML.read_text(encoding="utf-8")
    tag = "svyaml"
    while f"${tag}$" in yaml_text:
        tag += "x"

    con = duckdb.connect(str(db))
    try:
        con.execute("INSTALL semantic_views FROM community")
        con.execute("LOAD semantic_views")
        n_facts = con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        assert n_facts == LIVE_WEEK1_PICKS

        con.execute(
            f"CREATE OR REPLACE SEMANTIC VIEW nfl_ats_yaml FROM YAML ${tag}${yaml_text}${tag}$"
        )
        season = con.execute(
            """
            SELECT * FROM semantic_view(
              'nfl_ats_yaml',
              dimensions := ['season'],
              metrics := ['mean_no_vig_clv_pp', 'pick_count', 'ats_wins', 'ats_losses', 'ats_win_rate']
            )
            """
        ).fetchone()
        week = con.execute(
            """
            SELECT * FROM semantic_view(
              'nfl_ats_yaml',
              dimensions := ['week'],
              metrics := ['mean_no_vig_clv_pp', 'pick_count']
            )
            """
        ).fetchall()
    finally:
        # Leave DB clean of our named test view; fact rows untouched.
        try:
            con.execute("DROP SEMANTIC VIEW IF EXISTS nfl_ats_yaml")
        except Exception:
            pass
        con.close()

    assert season is not None
    # column order: season, mean_no_vig_clv_pp, pick_count, ats_wins, ats_losses, ats_win_rate
    assert season[2] == LIVE_WEEK1_PICKS
    assert season[3] == LIVE_ATS_WINS
    assert season[4] == LIVE_ATS_LOSSES
    assert abs(season[1] - LIVE_SEASON_MEAN_CLV) < 1e-9
    assert abs(season[5] - LIVE_ATS_WIN_RATE) < 1e-12
    assert week == [(1, pytest.approx(LIVE_SEASON_MEAN_CLV, abs=1e-9), LIVE_WEEK1_PICKS)]
