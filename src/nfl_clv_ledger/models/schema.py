"""DuckDB schema and export column order for the CLV ledger."""

from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ledger (
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
    bet_spread DOUBLE,
    close_spread DOUBLE,
    point_move DOUBLE,
    no_vig_clv_pp DOUBLE,
    clv_adj_pp DOUBLE,
    result VARCHAR NOT NULL DEFAULT 'pending',
    notes VARCHAR
);
"""

# Idempotent ALTERs for ledgers created before point-adjusted CLV.
MIGRATE_COLUMNS_SQL = [
    "ALTER TABLE ledger ADD COLUMN IF NOT EXISTS bet_spread DOUBLE",
    "ALTER TABLE ledger ADD COLUMN IF NOT EXISTS close_spread DOUBLE",
    "ALTER TABLE ledger ADD COLUMN IF NOT EXISTS point_move DOUBLE",
    "ALTER TABLE ledger ADD COLUMN IF NOT EXISTS clv_adj_pp DOUBLE",
]

LEDGER_COLUMNS = [
    "season",
    "week",
    "game_id",
    "side",
    "market",
    "book",
    "open_price",
    "bet_price",
    "close_price",
    "open_ts",
    "bet_ts",
    "close_ts",
    "bet_spread",
    "close_spread",
    "point_move",
    "no_vig_clv_pp",
    "clv_adj_pp",
    "result",
    "notes",
]

# Skill export / grade columns (subset order used in weekly CSV handoff)
SKILL_EXPORT_COLUMNS = [
    "week",
    "game_id",
    "side",
    "book",
    "open_price",
    "bet_price",
    "close_price",
    "bet_spread",
    "close_spread",
    "point_move",
    "no_vig_clv_pp",
    "clv_adj_pp",
    "result",
    "notes",
]

RESULT_VALUES = frozenset({"win", "loss", "push", "pending", "no_bet"})
MARKET_VALUES = frozenset({"ats", "ml"})
