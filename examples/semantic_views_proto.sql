-- DuckDB semantic_views prototype over the NFL CLV ledger fact table.
-- Docs: https://duckdb.org/community_extensions/extensions/semantic_views
-- Full reference: https://anentropic.github.io/duckdb-semantic-views/
--
-- Requires DuckDB with community extensions (verified on 1.5.5).
-- Does NOT modify fact rows in `ledger` — only CREATE/REPLACE SEMANTIC VIEW DDL.
--
-- Clause direction (Snowflake-style): alias.<logical_name> AS <sql_expression>
--   — the name comes BEFORE AS; the expression AFTER. Same for DIMENSIONS/METRICS/FACTS.
--
-- NULL handling: AVG(no_vig_clv_pp) skips NULL inputs (DuckDB default). Pending /
-- incomplete closes that leave no_vig_clv_pp NULL do not pull the mean toward zero.
-- ATS W–L metrics deliberately exclude push / pending / no_bet from the denominator.

INSTALL semantic_views FROM community;
LOAD semantic_views;

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
    -- Process: pick volume
    l.pick_count AS COUNT(*),
    -- Process: mean no-vig CLV in probability points (NULLs skipped by AVG)
    l.mean_no_vig_clv_pp AS AVG(l.no_vig_clv_pp),
    -- Outcome: ATS W–L kept separate from CLV
    l.ats_wins AS COUNT(*) FILTER (WHERE l.result = 'win'),
    l.ats_losses AS COUNT(*) FILTER (WHERE l.result = 'loss'),
    -- Derived metric (no table prefix): win rate over graded win/loss only
    ats_win_rate AS ats_wins * 1.0 / NULLIF(ats_wins + ats_losses, 0)
  );

-- ---------------------------------------------------------------------------
-- Demo queries (extension assembles GROUP BY)
-- ---------------------------------------------------------------------------

-- (a) Season mean CLV + pick_count (+ ATS W–L for contrast; keep separate)
SELECT * FROM semantic_view(
  'nfl_ats_process',
  dimensions := ['season'],
  metrics := ['mean_no_vig_clv_pp', 'pick_count', 'ats_wins', 'ats_losses', 'ats_win_rate']
);

-- (b) By week: mean CLV + pick_count
SELECT * FROM semantic_view(
  'nfl_ats_process',
  dimensions := ['week'],
  metrics := ['mean_no_vig_clv_pp', 'pick_count']
);

-- (c) By book: mean CLV (+ pick_count for context)
SELECT * FROM semantic_view(
  'nfl_ats_process',
  dimensions := ['book'],
  metrics := ['mean_no_vig_clv_pp', 'pick_count']
)
ORDER BY book;
