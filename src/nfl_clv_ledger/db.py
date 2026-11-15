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
    MIGRATE_COLUMNS_SQL,
    RESULT_VALUES,
    SCHEMA_SQL,
    SKILL_EXPORT_COLUMNS,
)
from nfl_clv_ledger.odds import (
    compute_clv_adj_pp,
    compute_clv_if_possible,
    compute_point_move,
    normalize_american_price,
)

DEFAULT_DB = "./ledger.duckdb"


def default_db_path() -> Path:
    return Path(os.environ.get("NFL_CLV_DB", DEFAULT_DB))


def migrate_ledger(con: duckdb.DuckDBPyConnection) -> None:
    """Idempotent schema migration: add point-CLV columns, keep existing rows.

    Does not recompute or rewrite no_vig_clv_pp.
    """
    for stmt in MIGRATE_COLUMNS_SQL:
        con.execute(stmt)


def connect(db_path: Optional[Path | str] = None) -> duckdb.DuckDBPyConnection:
    path = Path(db_path) if db_path is not None else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA_SQL)
    migrate_ledger(con)
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


def _normalize_optional_spread(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _clv_fields(
    bet_price: Optional[float],
    close_price: Optional[float],
    bet_spread: Optional[float],
    close_spread: Optional[float],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (no_vig_clv_pp, point_move, clv_adj_pp)."""
    clv = compute_clv_if_possible(bet_price, close_price)
    move = compute_point_move(bet_spread, close_spread)
    adj = compute_clv_adj_pp(bet_price, close_price, bet_spread, close_spread)
    return clv, move, adj


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
    bet_spread: Optional[float] = None,
    close_spread: Optional[float] = None,
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

    bet_s = _normalize_optional_spread(bet_spread)
    close_s = _normalize_optional_spread(close_spread)
    clv, move, adj = _clv_fields(bet_p, close_p, bet_s, close_s)
    row_id = _next_id(con)
    con.execute(
        """
        INSERT INTO ledger (
            id, season, week, game_id, side, market, book,
            open_price, bet_price, close_price,
            open_ts, bet_ts, close_ts,
            bet_spread, close_spread, point_move,
            no_vig_clv_pp, clv_adj_pp, result, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            bet_s,
            close_s,
            move,
            clv,
            adj,
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
                bet_spread=_f("bet_spread"),
                close_spread=_f("close_spread"),
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
    close_spread: Optional[float] = None,
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
        f"SELECT id, bet_price, bet_spread, close_spread FROM ledger WHERE {where}",
        params,
    ).fetchall()
    updated = 0
    for row_id, bet_price, bet_spread, existing_close_spread in rows:
        stored_close_spread = (
            float(close_spread) if close_spread is not None else existing_close_spread
        )
        clv, move, adj = _clv_fields(bet_price, close_p, bet_spread, stored_close_spread)
        con.execute(
            """
            UPDATE ledger
            SET close_price = ?,
                close_ts = COALESCE(?, close_ts),
                close_spread = ?,
                no_vig_clv_pp = ?,
                point_move = ?,
                clv_adj_pp = ?
            WHERE id = ?
            """,
            [close_p, close_ts, stored_close_spread, clv, move, adj, row_id],
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

    # Recompute price CLV, point move, and adjusted CLV from stored inputs.
    pending = con.execute(
        """
        SELECT id, bet_price, close_price, bet_spread, close_spread FROM ledger
        WHERE week = ?
          AND (? IS NULL OR season = ?)
        """,
        [week, season, season],
    ).fetchall()
    for row_id, bet_price, close_price, bet_spread, close_spread in pending:
        clv, move, adj = _clv_fields(bet_price, close_price, bet_spread, close_spread)
        con.execute(
            """
            UPDATE ledger
            SET no_vig_clv_pp = ?, point_move = ?, clv_adj_pp = ?
            WHERE id = ?
            """,
            [clv, move, adj, row_id],
        )

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
        SELECT no_vig_clv_pp, clv_adj_pp, point_move, result, market FROM ledger
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
        SELECT no_vig_clv_pp, clv_adj_pp, point_move, result, market FROM ledger
        WHERE (? IS NULL OR season = ?)
        """,
        [season, season],
    ).fetchall()
    return _summarize(rows, label="season")


def _summarize(rows: list[tuple], *, label: str) -> dict[str, Any]:
    clvs = [r[0] for r in rows if r[0] is not None]
    adjs = [r[1] for r in rows if r[1] is not None]
    moves = [r[2] for r in rows if r[2] is not None]
    ats = [r for r in rows if (r[4] or "ats") == "ats" and r[3] in ("win", "loss", "push")]
    wins = sum(1 for r in ats if r[3] == "win")
    losses = sum(1 for r in ats if r[3] == "loss")
    pushes = sum(1 for r in ats if r[3] == "push")
    mean_clv = sum(clvs) / len(clvs) if clvs else None
    mean_adj = sum(adjs) / len(adjs) if adjs else None
    mean_move = sum(moves) / len(moves) if moves else None
    pct_beat = (100.0 * sum(1 for m in moves if m > 0) / len(moves)) if moves else None
    return {
        "label": label,
        "n_rows": len(rows),
        "n_clv": len(clvs),
        "n_clv_adj": len(adjs),
        "n_point_move": len(moves),
        "mean_clv_pp": round(mean_clv, 4) if mean_clv is not None else None,
        "sum_clv_pp": round(sum(clvs), 4) if clvs else None,
        "mean_clv_adj_pp": round(mean_adj, 4) if mean_adj is not None else None,
        "mean_point_move": round(mean_move, 4) if mean_move is not None else None,
        "pct_beat_close": round(pct_beat, 1) if pct_beat is not None else None,
        "ats_wins": wins,
        "ats_losses": losses,
        "ats_pushes": pushes,
        "ats_record": f"{wins}-{losses}-{pushes}",
    }


def _fmt_pp(value: Optional[float]) -> str:
    return f"{value:+.4f} pp" if value is not None else "n/a"


def _fmt_move(value: Optional[float]) -> str:
    return f"{value:+.2f}" if value is not None else "n/a"


def _fmt_pct(value: Optional[float]) -> str:
    return f"{value:.1f}%" if value is not None else "n/a"


def format_summary_lines(summary: dict[str, Any], *, season_stats: Optional[dict[str, Any]] = None) -> list[str]:
    """Human-readable lines. Season CLV and ATS W-L are ALWAYS separate lines."""
    lines: list[str] = []
    if summary is not season_stats and summary.get("label", "").startswith("week"):
        lines.append(f"Week CLV (mean no-vig): {_fmt_pp(summary.get('mean_clv_pp'))}")
        lines.append(f"Week adjusted CLV (mean clv_adj_pp): {_fmt_pp(summary.get('mean_clv_adj_pp'))}")
        lines.append(f"Week avg point move: {_fmt_move(summary.get('mean_point_move'))}")
        lines.append(f"Week beat close (points): {_fmt_pct(summary.get('pct_beat_close'))}")
        lines.append(f"Week ATS W-L: {summary.get('ats_record', '0-0-0')}")
    target = season_stats or summary
    lines.append(f"Season CLV (mean no-vig): {_fmt_pp(target.get('mean_clv_pp'))}")
    lines.append(f"Season adjusted CLV (mean clv_adj_pp): {_fmt_pp(target.get('mean_clv_adj_pp'))}")
    lines.append(f"Avg point move: {_fmt_move(target.get('mean_point_move'))}")
    lines.append(f"Beat close (points): {_fmt_pct(target.get('pct_beat_close'))}")
    lines.append(f"ATS W-L: {target.get('ats_record', '0-0-0')}")
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
