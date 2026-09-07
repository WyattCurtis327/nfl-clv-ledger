"""Sample round-trip: add → set-close → grade → export; summary separates CLV and W-L."""

from __future__ import annotations

import csv
from pathlib import Path

from nfl_clv_ledger import db
from nfl_clv_ledger.models.schema import SKILL_EXPORT_COLUMNS


SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "week1_sample.csv"


def test_roundtrip_add_set_close_grade_export(tmp_path: Path):
    db_path = tmp_path / "ledger.duckdb"
    con = db.connect(db_path)
    try:
        n = db.add_from_csv(con, SAMPLE)
        assert n == 3

        closes = [
            ("KC@LAC_2026-09-06", "LAC", -120.0, "2026-09-06T12:00:00-07:00"),
            ("ARI@NO_2026-09-07", "ARI", -115.0, "2026-09-07T12:00:00-07:00"),
            ("SF@SEA_2026-09-07", "SF", -110.0, "2026-09-07T12:00:00-07:00"),
        ]
        for game_id, side, close_price, close_ts in closes:
            updated = db.set_close(
                con,
                season=2026,
                week=1,
                game_id=game_id,
                side=side,
                close_price=close_price,
                close_ts=close_ts,
            )
            assert updated == 1

        results = [
            {"season": 2026, "week": 1, "game_id": "KC@LAC_2026-09-06", "side": "LAC", "result": "win"},
            {"season": 2026, "week": 1, "game_id": "ARI@NO_2026-09-07", "side": "ARI", "result": "loss"},
            {"season": 2026, "week": 1, "game_id": "SF@SEA_2026-09-07", "side": "SF", "result": "push"},
        ]
        out = db.grade_week(con, week=1, season=2026, results=results)
        assert out["week"]["ats_record"] == "1-1-1"
        assert out["season"]["ats_record"] == "1-1-1"
        assert out["season"]["n_clv"] == 3
        assert out["season"]["mean_clv_pp"] is not None

        lines = db.format_summary_lines(out["week"], season_stats=out["season"])
        clv_lines = [ln for ln in lines if "Season CLV" in ln]
        wl_lines = [ln for ln in lines if ln.startswith("ATS W-L")]
        assert len(clv_lines) == 1
        assert len(wl_lines) == 1
        assert clv_lines[0] != wl_lines[0]

        export_path = tmp_path / "week1.csv"
        db.export_week_csv(con, week=1, season=2026, out_path=export_path)
        with export_path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames == SKILL_EXPORT_COLUMNS
            rows = list(reader)
        assert len(rows) == 3
        for row in rows:
            assert row["close_price"]
            assert row["no_vig_clv_pp"]
            assert row["result"] in {"win", "loss", "push"}

        season_stats = db.season_summary(con, season=2026)
        summary_lines = db.format_summary_lines(season_stats)
        assert summary_lines[0].startswith("Season CLV")
        assert summary_lines[1].startswith("ATS W-L")
    finally:
        con.close()


def test_cli_summary_separates_clv_and_wl(tmp_path: Path, capsys):
    from nfl_clv_ledger.cli import main

    db_path = tmp_path / "cli.duckdb"
    assert main(["--db", str(db_path), "init"]) == 0
    assert main(["--db", str(db_path), "add", "--from-csv", str(SAMPLE), "--season", "2026"]) == 0
    assert (
        main(
            [
                "--db",
                str(db_path),
                "set-close",
                "--season",
                "2026",
                "--week",
                "1",
                "--game-id",
                "KC@LAC_2026-09-06",
                "--side",
                "LAC",
                "--close-price",
                "-120",
            ]
        )
        == 0
    )
    assert main(["--db", str(db_path), "summary", "--season", "2026"]) == 0
    captured = capsys.readouterr().out.strip().splitlines()
    assert any(line.startswith("Season CLV") for line in captured)
    assert any(line.startswith("ATS W-L") for line in captured)
    for line in captured:
        if "Season CLV" in line:
            assert "ATS W-L" not in line
