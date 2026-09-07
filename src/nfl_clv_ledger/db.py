"""DuckDB persistence for the NFL CLV ledger."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Iterable, Optional

import duckdb

from nfl_clv_ledger.models.schema import (
    LEDGER_COLUMNS,
    MARKET_VALUES,
    RESULT_VALUES,
    SCHEMA_SQL,
    SKILL_EXPORT_COLUMNS,
)
from nfl_clv_ledger.odds import compute_clv_if_possible, normalize_american_price

DEFAULT_DB = "./ledger.duckdb"


def default_db_path() -> Path:
    return Path(os.environ.get("NFL_CLV_DB", DEFAULT_DB))


def connect(db_path: Optional[Path | str] = None) -> duckdb.DuckDBPyConnection:
    path = Path(db_path) if db_path is not None else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA_SQL)
    return con


def init_db(db_path: Optional[Path | str] = None) -> Path:
    path = Path(db_path) if db_path is not None else default_db_path()
    con = connect(path)
    con.close()
    return path


def _next_id(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM ledger").fetchone()
    return int(row[0])


def _validate_result(result: str) -> str:
    r = (result or "pending").strip().lower()
    if r not in RESULT_VALUES:
        raise ValueError(f"result must be one of {sorted(RESULT_VALUES)}, got {result!r}")
    return r


def _validate_market(market: str) -> str:
    m = (market or "ats").strip().lower()
    if m not in MARKET_VALUES:
        raise ValueError(f"market must be one of {sorted(MARKET_VALUES)}, got {market!r}")
    return m


def add_row(
    con: duckdb.DuckDBPyConnection,
    *,
    season: int,
    week: int,
    game_id: str,
    side: str,
    market: str = "ats",
    book: Optional[str] = None,
    open_price: Optional[float] = None,
    bet_price: Optional[float] = None,
    close_price: Optional[float] = None,
    open_ts: Optional[str] = None,
    bet_ts: Optional[str] = None,
    close_ts: Optional[str] = None,
    result: str = "pending",
    notes: Optional[str] = None,
) -> int:
    """Insert one ledger row; compute CLV when bet+close present."""
    market = _validate_market(market)
    result = _validate_result(result)

    # Assume -110 when a price field is present but not valid American (ATS v1).
    open_p = normalize_american_price(open_price) if open_price is not None else None
    bet_p = normalize_american_price(bet_price) if bet_price is not None else None
    close_p = normalize_american_price(close_price) if close_price is not None else None

    # If caller passed an explicit numeric that normalize maps (e.g. bare 0),
    # prefer stored American; if they omitted juice entirely (None), leave None
    # except when they passed a non-American sentinel — normalize handles it.
    if open_price is not None and open_p is None:
        open_p = -110.0
    if bet_price is not None and bet_p is None:
        bet_p = -110.0
    if close_price is not None and close_p is None:
        close_p = -110.0

    clv = compute_clv_if_possible(bet_p, close_p)
    row_id = _next_id(con)
    con.execute(
        """
        INSERT INTO ledger (
            id, season, week, game_id, side, market, book,
            open_price, bet_price, close_price,
            open_ts, bet_ts, close_ts,
            no_vig_clv_pp, result, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row_id,
            int(season),
            int(week),
            str(game_id),
            str(side),
            market,
            book,
            open_p,
            bet_p,
            close_p,
            open_ts,
            bet_ts,
            close_ts,
            clv,
            result,
            notes,
        ],
    )
    return row_id


def add_from_csv(
    con: duckdb.DuckDBPyConnection,
    csv_path: Path | str,
    *,
    default_season: Optional[int] = None,
) -> int:
    """Load rows from CSV. Returns count inserted."""
    path = Path(csv_path)
    count = 0
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
            season = row.get("season") or default_season
            if season is None or season == "":
                raise ValueError("CSV row missing season (pass --season or include column)")
            week = row.get("week")
            if week is None or week == "":
                raise ValueError("CSV row missing week")

            def _f(key: str) -> Optional[float]:
                val = row.get(key)
                if val is None or val == "":
                    return None
                return float(val)

            add_row(
                con,
                season=int(season),
                week=int(week),
                game_id=row.get("game_id") or "",
                side=row.get("side") or "",
                market=row.get("market") or "ats",
                book=row.get("book") or None,
                open_price=_f("open_price"),
                bet_price=_f("bet_price"),
                close_price=_f("close_price"),
                open_ts=row.get("open_ts") or None,
                bet_ts=row.get("bet_ts") or None,
                close_ts=row.get("close_ts") or None,
                result=row.get("result") or "pending",
                notes=row.get("notes") or None,
            )
            count += 1
    return count


def set_close(
    con: duckdb.DuckDBPyConnection,
    *,
    season: int,
    week: int,
    game_id: str,
    side: str,
    close_price: float,
    close_ts: Optional[str] = None,
    market: Optional[str] = None,
) -> int:
    """Fill close price (and recompute CLV) for matching row(s). Returns rows updated."""
    close_p = normalize_american_price(close_price)
    if close_p is None:
        close_p = -110.0

    where = "season = ? AND week = ? AND game_id = ? AND side = ?"
    params: list[Any] = [int(season), int(week), str(game_id), str(side)]
    if market:
        where += " AND market = ?"
        params.append(_validate_market(market))

    rows = con.execute(
        f"SELECT id, bet_price FROM ledger WHERE {where}",
        params,
    ).fetchall()
    updated = 0
    for row_id, bet_price in rows:
        clv = compute_clv_if_possible(bet_price, close_p)
        con.execute(
            """
            UPDATE ledger
            SET close_price = ?, close_ts = COALESCE(?, close_ts), no_vig_clv_pp = ?
            WHERE id = ?
            """,
            [close_p, close_ts, clv, row_id],
        )
        updated += 1
    return updated


def set_result(
    con: duckdb.DuckDBPyConnection,
    *,
    season: int,
    week: int,
    game_id: str,
    side: str,
    result: str,
    market: Optional[str] = None,
) -> int:
    result = _validate_result(result)
    where = "season = ? AND week = ? AND game_id = ? AND side = ?"
    params: list[Any] = [int(season), int(week), str(game_id), str(side)]
    if market:
        where += " AND market = ?"
        params.append(_validate_market(market))
    before = con.execute(f"SELECT id FROM ledger WHERE {where}", params).fetchall()
    if not before:
        return 0
    con.execute(f"UPDATE ledger SET result = ? WHERE {where}", [result, *params])
    return len(before)


def grade_week(
    con: duckdb.DuckDBPyConnection,
    *,
    week: int,
    season: Optional[int] = None,
    results: Optional[Iterable[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Optionally apply results, recompute CLV for the week, return summary stats."""
    if results:
        for item in results:
            set_result(
                con,
                season=int(item["season"] if "season" in item else season),
                week=int(item.get("week", week)),
                game_id=str(item["game_id"]),
                side=str(item["side"]),
                result=str(item["result"]),
                market=item.get("market"),
            )

    # Recompute CLV for any rows that have bet+close but null clv
    pending_clv = con.execute(
        """
        SELECT id, bet_price, close_price FROM ledger
        WHERE week = ?
          AND (? IS NULL OR season = ?)
          AND bet_price IS NOT NULL AND close_price IS NOT NULL
        """,
        [week, season, season],
    ).fetchall()
    for row_id, bet_price, close_price in pending_clv:
        clv = compute_clv_if_possible(bet_price, close_price)
        con.execute("UPDATE ledger SET no_vig_clv_pp = ? WHERE id = ?", [clv, row_id])

    return {
        "week": week_summary(con, week=week, season=season),
        "season": season_summary(con, season=season),
    }


def week_summary(
    con: duckdb.DuckDBPyConnection,
    *,
    week: int,
    season: Optional[int] = None,
) -> dict[str, Any]:
    rows = con.execute(
        """
        SELECT no_vig_clv_pp, result, market FROM ledger
        WHERE week = ? AND (? IS NULL OR season = ?)
        """,
        [week, season, season],
    ).fetchall()
    return _summarize(rows, label=f"week {week}")


def season_summary(
    con: duckdb.DuckDBPyConnection,
    *,
    season: Optional[int] = None,
) -> dict[str, Any]:
    rows = con.execute(
        """
        SELECT no_vig_clv_pp, result, market FROM ledger
        WHERE (? IS NULL OR season = ?)
        """,
        [season, season],
    ).fetchall()
    return _summarize(rows, label="season")


def _summarize(rows: list[tuple], *, label: str) -> dict[str, Any]:
    clvs = [r[0] for r in rows if r[0] is not None]
    ats = [r for r in rows if (r[2] or "ats") == "ats" and r[1] in ("win", "loss", "push")]
    wins = sum(1 for r in ats if r[1] == "win")
    losses = sum(1 for r in ats if r[1] == "loss")
    pushes = sum(1 for r in ats if r[1] == "push")
    mean_clv = sum(clvs) / len(clvs) if clvs else None
    return {
        "label": label,
        "n_rows": len(rows),
        "n_clv": len(clvs),
        "mean_clv_pp": round(mean_clv, 4) if mean_clv is not None else None,
        "sum_clv_pp": round(sum(clvs), 4) if clvs else None,
        "ats_wins": wins,
        "ats_losses": losses,
        "ats_pushes": pushes,
        "ats_record": f"{wins}-{losses}-{pushes}",
    }


def format_summary_lines(summary: dict[str, Any], *, season_stats: Optional[dict[str, Any]] = None) -> list[str]:
    """Human-readable lines. Season CLV and ATS W-L are ALWAYS separate lines."""
    lines: list[str] = []
    target = season_stats or summary
    clv = target.get("mean_clv_pp")
    clv_s = f"{clv:+.4f} pp" if clv is not None else "n/a"
    # Separate lines — never conflate
    lines.append(f"Season CLV (mean no-vig): {clv_s}")
    lines.append(f"ATS W-L: {target.get('ats_record', '0-0-0')}")
    if summary is not season_stats and summary.get("label", "").startswith("week"):
        wclv = summary.get("mean_clv_pp")
        wclv_s = f"{wclv:+.4f} pp" if wclv is not None else "n/a"
        lines.insert(0, f"Week CLV (mean no-vig): {wclv_s}")
        lines.insert(1, f"Week ATS W-L: {summary.get('ats_record', '0-0-0')}")
    return lines


def export_week_csv(
    con: duckdb.DuckDBPyConnection,
    *,
    week: int,
    out_path: Path | str,
    season: Optional[int] = None,
    skill_columns: bool = True,
) -> Path:
    cols = SKILL_EXPORT_COLUMNS if skill_columns else LEDGER_COLUMNS
    col_sql = ", ".join(cols)
    rows = con.execute(
        f"""
        SELECT {col_sql} FROM ledger
        WHERE week = ? AND (? IS NULL OR season = ?)
        ORDER BY id
        """,
        [week, season, season],
    ).fetchall()
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols)
        for row in rows:
            writer.writerow(list(row))
    return path


def fetch_all(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = con.execute(
        f"SELECT {', '.join(LEDGER_COLUMNS)} FROM ledger ORDER BY id"
    ).fetchall()
    return [dict(zip(LEDGER_COLUMNS, r)) for r in rows]
