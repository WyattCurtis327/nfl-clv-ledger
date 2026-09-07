"""CLI entrypoint: nfl-clv."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from nfl_clv_ledger import db


def _db_path(args: argparse.Namespace) -> Optional[Path]:
    if getattr(args, "db", None):
        return Path(args.db)
    return None


def cmd_init(args: argparse.Namespace) -> int:
    path = db.init_db(_db_path(args))
    print(f"initialized ledger at {path}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    con = db.connect(_db_path(args))
    try:
        if args.from_csv:
            n = db.add_from_csv(con, args.from_csv, default_season=args.season)
            print(f"added {n} row(s) from {args.from_csv}")
            return 0
        if args.season is None or args.week is None or not args.game_id or not args.side:
            print(
                "add requires --season --week --game-id --side (or --from-csv)",
                file=sys.stderr,
            )
            return 2
        row_id = db.add_row(
            con,
            season=args.season,
            week=args.week,
            game_id=args.game_id,
            side=args.side,
            market=args.market,
            book=args.book,
            open_price=args.open_price,
            bet_price=args.bet_price,
            close_price=args.close_price,
            open_ts=args.open_ts,
            bet_ts=args.bet_ts,
            close_ts=args.close_ts,
            result=args.result,
            notes=args.notes,
        )
        print(f"added row id={row_id}")
        return 0
    finally:
        con.close()


def cmd_set_close(args: argparse.Namespace) -> int:
    if args.season is None or args.week is None or not args.game_id or not args.side:
        print("set-close requires --season --week --game-id --side --close-price", file=sys.stderr)
        return 2
    if args.close_price is None:
        print("set-close requires --close-price", file=sys.stderr)
        return 2
    con = db.connect(_db_path(args))
    try:
        n = db.set_close(
            con,
            season=args.season,
            week=args.week,
            game_id=args.game_id,
            side=args.side,
            close_price=args.close_price,
            close_ts=args.close_ts,
            market=args.market,
        )
        print(f"updated close on {n} row(s)")
        return 0 if n else 1
    finally:
        con.close()


def cmd_grade(args: argparse.Namespace) -> int:
    if args.week is None:
        print("grade requires --week", file=sys.stderr)
        return 2
    con = db.connect(_db_path(args))
    try:
        results = None
        if args.results_json:
            results = json.loads(Path(args.results_json).read_text(encoding="utf-8"))
        out = db.grade_week(con, week=args.week, season=args.season, results=results)
        lines = db.format_summary_lines(out["week"], season_stats=out["season"])
        for line in lines:
            print(line)
        return 0
    finally:
        con.close()


def cmd_export(args: argparse.Namespace) -> int:
    if args.week is None or not args.output:
        print("export requires --week and -o/--output", file=sys.stderr)
        return 2
    con = db.connect(_db_path(args))
    try:
        path = db.export_week_csv(
            con,
            week=args.week,
            out_path=args.output,
            season=args.season,
            skill_columns=not args.full_columns,
        )
        print(f"wrote {path}")
        return 0
    finally:
        con.close()


def cmd_summary(args: argparse.Namespace) -> int:
    con = db.connect(_db_path(args))
    try:
        season_stats = db.season_summary(con, season=args.season)
        for line in db.format_summary_lines(season_stats):
            print(line)
        return 0
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nfl-clv",
        description="NFL ATS/ML closing-line value ledger (process ≠ W-L).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="DuckDB path (default: $NFL_CLV_DB or /workspace/nfl-analytics/clv/ledger.duckdb)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create DB + schema")
    p_init.set_defaults(func=cmd_init)

    p_add = sub.add_parser("add", help="Log a pick or import CSV")
    p_add.add_argument("--season", type=int, default=None)
    p_add.add_argument("--week", type=int, default=None)
    p_add.add_argument("--game-id", dest="game_id", default=None)
    p_add.add_argument("--side", default=None)
    p_add.add_argument("--market", default="ats", choices=["ats", "ml"])
    p_add.add_argument("--book", default=None)
    p_add.add_argument("--open-price", dest="open_price", type=float, default=None)
    p_add.add_argument("--bet-price", dest="bet_price", type=float, default=None)
    p_add.add_argument("--close-price", dest="close_price", type=float, default=None)
    p_add.add_argument("--open-ts", dest="open_ts", default=None)
    p_add.add_argument("--bet-ts", dest="bet_ts", default=None)
    p_add.add_argument("--close-ts", dest="close_ts", default=None)
    p_add.add_argument("--result", default="pending")
    p_add.add_argument("--notes", default=None)
    p_add.add_argument("--from-csv", dest="from_csv", default=None)
    p_add.set_defaults(func=cmd_add)

    p_close = sub.add_parser("set-close", help="Fill close price / Monday catch-up")
    p_close.add_argument("--season", type=int, required=True)
    p_close.add_argument("--week", type=int, required=True)
    p_close.add_argument("--game-id", dest="game_id", required=True)
    p_close.add_argument("--side", required=True)
    p_close.add_argument("--close-price", dest="close_price", type=float, required=True)
    p_close.add_argument("--close-ts", dest="close_ts", default=None)
    p_close.add_argument("--market", default=None, choices=["ats", "ml"])
    p_close.set_defaults(func=cmd_set_close)

    p_grade = sub.add_parser("grade", help="Grade week; print week + season CLV and ATS W-L")
    p_grade.add_argument("--week", type=int, required=True)
    p_grade.add_argument("--season", type=int, default=None)
    p_grade.add_argument(
        "--results-json",
        dest="results_json",
        default=None,
        help="Optional JSON list of {season,week,game_id,side,result[,market]}",
    )
    p_grade.set_defaults(func=cmd_grade)

    p_export = sub.add_parser("export", help="Export week CSV for Analytics")
    p_export.add_argument("--week", type=int, required=True)
    p_export.add_argument("--season", type=int, default=None)
    p_export.add_argument("-o", "--output", required=True)
    p_export.add_argument(
        "--full-columns",
        action="store_true",
        help="Include full ledger columns instead of skill subset",
    )
    p_export.set_defaults(func=cmd_export)

    p_sum = sub.add_parser("summary", help="Season cumulative CLV and ATS W-L (separate lines)")
    p_sum.add_argument("--season", type=int, default=None)
    p_sum.set_defaults(func=cmd_summary)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
