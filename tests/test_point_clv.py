"""Point-adjusted CLV: migration, CLI wiring, and no_vig_clv_pp regression."""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pytest

from nfl_clv_ledger import db
from nfl_clv_ledger.cli import main
from nfl_clv_ledger.models.schema import LEDGER_COLUMNS, SKILL_EXPORT_COLUMNS
from nfl_clv_ledger.odds import compute_clv_if_possible, no_vig_clv_pp, point_value_pp

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "week1_sample.csv"

# Frozen juice-only CLV for the week1 sample closes used in test_roundtrip.py.
# These must not change when spreads / clv_adj_pp are added.
FROZEN_NO_VIG_CLV_PP = {
    ("KC@LAC_2026-09-06", "LAC"): 1.012146,  # bet -110, close -120
    ("ARI@NO_2026-09-07", "ARI"): 0.742503,  # bet -108, close -115
    ("SF@SEA_2026-09-07", "SF"): -0.523013,  # bet -115, close -110
}

SAMPLE_CLOSES = [
    ("KC@LAC_2026-09-06", "LAC", -120.0),
    ("ARI@NO_2026-09-07", "ARI", -115.0),
    ("SF@SEA_2026-09-07", "SF", -110.0),
]


def _apply_sample_closes(con, *, close_spreads=None):
    close_spreads = close_spreads or {}
    for game_id, side, close_price in SAMPLE_CLOSES:
        n = db.set_close(
            con,
            season=2026,
            week=1,
            game_id=game_id,
            side=side,
            close_price=close_price,
            close_spread=close_spreads.get((game_id, side)),
        )
        assert n == 1


def test_no_vig_clv_pp_regression_frozen_values(tmp_path: Path):
    """Existing no_vig_clv_pp values stay juice-only and match the frozen list."""
    db_path = tmp_path / "ledger.duckdb"
    con = db.connect(db_path)
    try:
        assert db.add_from_csv(con, SAMPLE) == 3
        _apply_sample_closes(con)
        rows = { (r["game_id"], r["side"]): r for r in db.fetch_all(con) }
        for key, expected in FROZEN_NO_VIG_CLV_PP.items():
            got = rows[key]["no_vig_clv_pp"]
            juice = no_vig_clv_pp(
                rows[key]["bet_price"], rows[key]["close_price"]
            )
            assert got == pytest.approx(expected)
            assert got == pytest.approx(juice)
            assert compute_clv_if_possible(rows[key]["bet_price"], rows[key]["close_price"]) == pytest.approx(expected)
            # No spreads on this import → adjusted CLV stays null (not price-only).
            assert rows[key]["clv_adj_pp"] is None
            assert rows[key]["point_move"] is None
    finally:
        con.close()


def test_spreads_do_not_change_no_vig_clv_pp(tmp_path: Path):
    db_path = tmp_path / "ledger.duckdb"
    con = db.connect(db_path)
    try:
        db.add_from_csv(con, SAMPLE)
        _apply_sample_closes(con)
        before = { (r["game_id"], r["side"]): r["no_vig_clv_pp"] for r in db.fetch_all(con) }

        # Attach a 3-point beat on the first row only.
        db.set_close(
            con,
            season=2026,
            week=1,
            game_id="KC@LAC_2026-09-06",
            side="LAC",
            close_price=-120.0,
            close_spread=-13.5,
        )
        # bet_spread still null → still no point term
        row = next(r for r in db.fetch_all(con) if r["game_id"] == "KC@LAC_2026-09-06")
        assert row["no_vig_clv_pp"] == pytest.approx(before[("KC@LAC_2026-09-06", "LAC")])
        assert row["clv_adj_pp"] is None

        # Now insert a fresh row with both spreads; juice CLV must match the helper.
        db.add_row(
            con,
            season=2026,
            week=2,
            game_id="DEMO@TEST_2026-09-13",
            side="DEMO",
            bet_price=-110,
            close_price=-120,
            bet_spread=-10.5,
            close_spread=-13.5,
        )
        demo = next(r for r in db.fetch_all(con) if r["game_id"] == "DEMO@TEST_2026-09-13")
        assert demo["no_vig_clv_pp"] == pytest.approx(1.012146)
        assert demo["point_move"] == pytest.approx(3.0)
        assert demo["clv_adj_pp"] == pytest.approx(1.012146 + 5.9)
    finally:
        con.close()


def test_migrate_idempotent_keeps_existing_rows(tmp_path: Path):
    db_path = tmp_path / "old.duckdb"
    con = duckdb.connect(str(db_path))
    try:
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
        con.execute(
            """
            INSERT INTO ledger (
                id, season, week, game_id, side, market, book,
                open_price, bet_price, close_price,
                open_ts, bet_ts, close_ts, no_vig_clv_pp, result, notes
            ) VALUES (1, 2026, 1, 'OLD@ROW', 'OLD', 'ats', 'book',
                      -110, -110, -120, NULL, NULL, NULL, 1.012146, 'win', 'legacy')
            """
        )
        con.close()

        # connect() runs the idempotent migration.
        con = db.connect(db_path)
        db.migrate_ledger(con)  # second call must be a no-op
        rows = db.fetch_all(con)
        assert len(rows) == 1
        row = rows[0]
        assert row["game_id"] == "OLD@ROW"
        assert row["no_vig_clv_pp"] == pytest.approx(1.012146)
        assert row["bet_spread"] is None
        assert row["close_spread"] is None
        assert row["point_move"] is None
        assert row["clv_adj_pp"] is None
        assert row["result"] == "win"
        assert row["notes"] == "legacy"
    finally:
        con.close()


def test_cli_add_set_close_grade_export_summary(tmp_path: Path, capsys):
    db_path = tmp_path / "cli.duckdb"
    assert main(["--db", str(db_path), "init"]) == 0
    assert (
        main(
            [
                "--db", str(db_path), "add",
                "--season", "2026", "--week", "1",
                "--game-id", "KC@LAC_2026-09-06", "--side", "LAC",
                "--bet-price", "-110", "--bet-spread", "-10.5",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--db", str(db_path), "set-close",
                "--season", "2026", "--week", "1",
                "--game-id", "KC@LAC_2026-09-06", "--side", "LAC",
                "--close-price", "-120", "--close-spread", "-13.5",
            ]
        )
        == 0
    )
    assert main(["--db", str(db_path), "grade", "--week", "1", "--season", "2026"]) == 0
    grade_out = capsys.readouterr().out
    assert "Week CLV (mean no-vig):" in grade_out
    assert "Week adjusted CLV (mean clv_adj_pp):" in grade_out
    assert "Week avg point move:" in grade_out
    assert "Week beat close (points):" in grade_out
    assert "Season CLV (mean no-vig):" in grade_out
    assert "Season adjusted CLV (mean clv_adj_pp):" in grade_out
    assert "Avg point move:" in grade_out
    assert "Beat close (points):" in grade_out
    assert "ATS W-L:" in grade_out

    export_path = tmp_path / "week1.csv"
    assert main(["--db", str(db_path), "export", "--week", "1", "--season", "2026", "-o", str(export_path)]) == 0
    with export_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == SKILL_EXPORT_COLUMNS
        rows = list(reader)
    assert len(rows) == 1
    assert float(rows[0]["bet_spread"]) == -10.5
    assert float(rows[0]["close_spread"]) == -13.5
    assert float(rows[0]["point_move"]) == pytest.approx(3.0)
    assert float(rows[0]["no_vig_clv_pp"]) == pytest.approx(1.012146)
    assert float(rows[0]["clv_adj_pp"]) == pytest.approx(1.012146 + 5.9)

    full_path = tmp_path / "week1_full.csv"
    assert main(["--db", str(db_path), "export", "--week", "1", "--season", "2026", "-o", str(full_path), "--full-columns"]) == 0
    with full_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == LEDGER_COLUMNS

    assert main(["--db", str(db_path), "summary", "--season", "2026"]) == 0
    summary = capsys.readouterr().out.strip().splitlines()
    assert any(ln.startswith("Season CLV (mean no-vig)") for ln in summary)
    assert any(ln.startswith("Season adjusted CLV") for ln in summary)
    assert any(ln.startswith("Avg point move") for ln in summary)
    assert any(ln.startswith("Beat close (points)") for ln in summary)
    assert any(ln.startswith("ATS W-L") for ln in summary)
    # 100% beat close on the single row with a +3 point_move
    beat = next(ln for ln in summary if ln.startswith("Beat close (points)"))
    assert "100.0%" in beat
    move = next(ln for ln in summary if ln.startswith("Avg point move"))
    assert "+3.00" in move
    for ln in summary:
        if "Season CLV" in ln:
            assert "ATS W-L" not in ln


def test_cli_migrate(tmp_path: Path, capsys):
    db_path = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(db_path))
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
    con.execute(
        "INSERT INTO ledger (id, season, week, game_id, side, market, no_vig_clv_pp, result) "
        "VALUES (1, 2026, 1, 'X@Y', 'X', 'ats', 1.012146, 'pending')"
    )
    con.close()

    assert main(["--db", str(db_path), "migrate"]) == 0
    out = capsys.readouterr().out
    assert "migrated ledger at" in out
    con = db.connect(db_path)
    try:
        row = db.fetch_all(con)[0]
        assert row["no_vig_clv_pp"] == pytest.approx(1.012146)
        assert row["bet_spread"] is None
    finally:
        con.close()


def test_csv_import_reads_spreads(tmp_path: Path):
    csv_path = tmp_path / "with_spreads.csv"
    csv_path.write_text(
        "season,week,game_id,side,market,bet_price,close_price,bet_spread,close_spread,result\n"
        "2026,1,A@B,A,ats,-110,-110,-3.0,-3.5,pending\n",
        encoding="utf-8",
    )
    con = db.connect(tmp_path / "ledger.duckdb")
    try:
        assert db.add_from_csv(con, csv_path) == 1
        row = db.fetch_all(con)[0]
        assert row["bet_spread"] == pytest.approx(-3.0)
        assert row["close_spread"] == pytest.approx(-3.5)
        assert row["point_move"] == pytest.approx(0.5)
        assert row["no_vig_clv_pp"] == pytest.approx(0.0)
        assert row["clv_adj_pp"] == pytest.approx(point_value_pp(-3.0, -3.5))
    finally:
        con.close()


def test_pct_beat_close_ignores_zero_moves(tmp_path: Path):
    con = db.connect(tmp_path / "ledger.duckdb")
    try:
        db.add_row(con, season=2026, week=1, game_id="A@B", side="A", bet_price=-110, close_price=-110, bet_spread=-3.0, close_spread=-6.0)
        db.add_row(con, season=2026, week=1, game_id="C@D", side="C", bet_price=-110, close_price=-110, bet_spread=-3.0, close_spread=-3.0)
        db.add_row(con, season=2026, week=1, game_id="E@F", side="E", bet_price=-110, close_price=-110, bet_spread=-3.5, close_spread=-3.0)
        stats = db.season_summary(con, season=2026)
        assert stats["n_point_move"] == 3
        # 1 beat, 1 zero, 1 worse → 33.3%
        assert stats["pct_beat_close"] == pytest.approx(33.3)
        assert stats["mean_point_move"] == pytest.approx((3.0 + 0.0 + (-0.5)) / 3, abs=1e-4)
    finally:
        con.close()
