# nfl-clv-ledger

Small Python CLI + DuckDB ledger for NFL ATS (and optional ML) picks. Logs open / bet / close prices and grades **closing-line value (CLV)** separately from win–loss.

Consumer: NFL Analytics. Aligns with skill `nfl-clv-ledger`.

## Concepts

### Closing-line value (CLV)

**CLV** asks: did you get a better price than the market’s **final** number before kickoff?

Example: you bet Rams −3; the close ends −4. You beat the close by a point — that is **positive CLV**. That is good *process* even if the Rams lose ATS. Negative CLV means you got a worse number than the close.

Win rate over a small sample is mostly noise. Sustained positive CLV is the honest process metric against a sharp(ish) close.

### No-vig CLV

Books build a margin (**vig** / juice) into both sides of a market (e.g. −110 / −110). Raw prices mix edge with that tax.

**No-vig CLV** strips the vig so you compare **fair implied probabilities**, then measures how your bet price differs from the no-vig close in **probability points** (`no_vig_clv_pp`).

- Positive `no_vig_clv_pp` → you bought cheaper probability than the fair close.
- Break-even at −110 ≈ **52.38%** implied (reference only; not a target).

This ledger grades **no-vig CLV** and **ATS W–L** as separate lines. Never claim “beats the market” from W–L alone.

### Open / bet / close

| Field | Meaning |
|-------|---------|
| **Open** | First usable line you saw (or book open) for that side |
| **Bet** | Price you actually took (or would have taken) |
| **Close** | Last sharp-ish number before kickoff |

Always timestamp lines when possible. If close is missing after the slate, mark pending and catch up Monday (`set-close`).

### ATS vs process

- **ATS W–L** = did the side cover? High variance week to week.
- **CLV / no-vig CLV** = did you beat the close on price? Process score.

A week can be 0–3 ATS and still show positive CLV (or the reverse). Report both; do not conflate them.

### No-bet weeks

If Analytics looked at a slate and took **zero** plays, log an explicit no-bet row for that week so empty weeks are intentional, not missing data.

## Non-goals (v1)

- Live book / Odds API keys in the repo (manual or CSV import is enough)
- Claiming profitability or “beats Vegas” from this tool alone
- Survivor / pick’em optimizers (separate tools)

## CLI (v1)

```bash
nfl-clv init                 # create DB + schema
nfl-clv migrate              # add point-CLV columns on an existing ledger (idempotent)
nfl-clv add …                # log a pick (or --from-csv); optional --bet-spread / --close-spread
nfl-clv set-close …          # fill closes / Monday catch-up; optional --close-spread
nfl-clv grade --week N       # week + season CLV and ATS W–L (separate)
nfl-clv export --week N -o … # CSV for Analytics (includes spreads / adjusted CLV)
nfl-clv summary              # season price-only CLV, adjusted CLV, point move, W–L
```

Default DB path: `./ledger.duckdb` (cwd-relative), or override with env `NFL_CLV_DB`. On this agent box you can use `NFL_CLV_DB=/workspace/nfl-analytics/clv/ledger.duckdb`.

## Schema (skill-aligned)

`season | week | game_id | side | market | book | open_price | bet_price | close_price | open_ts | bet_ts | close_ts | bet_spread | close_spread | point_move | no_vig_clv_pp | clv_adj_pp | result | notes`

`market`: `ats` | `ml`. `result`: win / loss / push / pending / no_bet.

`bet_spread` and `close_spread` are signed from **our side’s** perspective (we bet −10.5 → `bet_spread = −10.5`; we bet a dog at +7.5 → `+7.5`). They are nullable; existing rows stay intact after `nfl-clv migrate`.

## Math notes (v1)

### American ↔ implied

- Favorite (≤ −100): `p = |a| / (|a| + 100)`
- Dog (≥ +100): `p = 100 / (a + 100)`
- Break-even at −110 ≈ **52.38%** raw implied (reference only).

### No-vig method: **multiplicative**

For a two-way with raw implieds `p_a`, `p_b`:

```text
no_vig_a = p_a / (p_a + p_b)
```

When a row logs only one side’s American juice (typical ATS), v1 synthesizes the
opposite at **−110** (standard ATS juice) for the multiplicative pair. If you
omit juice entirely on open/bet/close, the ledger **assumes −110** and stores
that American price.

### `no_vig_clv_pp`

```text
no_vig_clv_pp = 100 × (close_no_vig_prob − bet_no_vig_prob)
```

Units are **probability points** (not raw juice ticks). **Positive** means you
bought cheaper no-vig probability than the fair close (beat the close) — same
sign as the Concepts glossary above. Process ≠ profitability; never conflate
CLV with ATS W–L. This juice-only number is **unchanged** when spreads are
logged; spread moves go into `clv_adj_pp` instead.

### Signed spreads and `point_move`

Spreads are stored from our side’s perspective. `point_move` is **bet minus close**,
so it is positive when we beat the number:

```text
point_move = bet_spread − close_spread
```

Example: we bet −10.5 and it closed −13.5 → `point_move = +3`. (A raw
“close minus bet” would be −3 and would not match “positive when we beat the
number.”)

### Key-number push-probability table

A simple NFL table of `P(final margin = n)` in probability points
(`100 × P(n)`). Heavier weight on **3, 7, 6, 10, 14**. Integers not listed use
`DEFAULT_PUSH_PP = 0.4`.

```text
n     pp
0     0.3
1     3.3
2     3.5
3    15.0
4     5.2
5     3.1
6     6.4
7     9.4
8     3.2
9     2.1
10    6.2
11    2.0
12    1.5
13    2.4
14    5.4
15    1.3
16    1.6
17    3.5
18    1.0
19    0.8
20    2.0
21    2.8
22    0.6
23    0.7
24    2.0
25    0.5
26    0.6
27    1.2
28    1.5
```

This is a documented conversion, not a book model, and it is not a claim of
profitability.

### Point-value term and `clv_adj_pp`

Walk every **0.5-point step** from `bet_spread` to `close_spread`. For each
step, the key integer `n` is the integer endpoint of that step (one end is
always an integer). The step contributes `P(n) / 2`. Summing both half-steps
around `n` (win→push and push→loss) equals `P(n)` once — the change in
cover probability, which is the same unit as `no_vig_clv_pp`. The sign of the
sum follows `point_move`.

```text
point_value_pp = sign(point_move) × Σ  (P(n) / 2)  over each 0.5 step
clv_adj_pp     = no_vig_clv_pp + point_value_pp
```

Worked example: bet −10.5, close −13.5. Steps cross keys 11, 11, 12, 12, 13, 13
→ `point_value_pp = 2.0 + 1.5 + 2.4 = +5.9`. If the juices also moved from
−110 to −120 (`no_vig_clv_pp ≈ +1.012146`), then
`clv_adj_pp ≈ +6.912146`.

Crossing the 3: bet −3.0, close −3.5 → one half-step → `+15.0 / 2 = +7.5`.

### Null policy

`point_move` is NULL unless both spreads are present. `clv_adj_pp` is NULL
unless both spreads **and** `no_vig_clv_pp` are present. Missing spreads do
**not** silently fall back to price-only CLV; averaging a mix of adjusted and
price-only rows would be misleading. `summary` reports price-only CLV (mean
`no_vig_clv_pp`), adjusted CLV (mean of non-null `clv_adj_pp`), average
`point_move`, and the percent of non-null `point_move` rows that are
positive (zeros do not count as beating the close) as **separate lines** from
ATS W–L.

Existing ledgers: run `nfl-clv migrate` (or any command; `connect()` applies
the same idempotent `ADD COLUMN IF NOT EXISTS`). New columns are NULL until
you backfill spreads; `no_vig_clv_pp` is not rewritten.

## Quick start

Requires **Python 3.11+** (`requires-python` in `pyproject.toml`).

```bash
git clone https://github.com/WyattCurtis327/nfl-clv-ledger.git
cd nfl-clv-ledger
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
export NFL_CLV_DB=./ledger.duckdb
nfl-clv init
nfl-clv add --from-csv samples/week1_sample.csv
nfl-clv grade --week 1
nfl-clv summary
nfl-clv export --week 1 -o /tmp/week1.csv
```

The sample includes `close_price` and graded `result` values so `grade` / `summary` print numeric Season CLV (not `n/a`) and ATS W–L.

## Install / test

```bash
pip install -e ".[dev]"
pytest
```



## DuckDB semantic views prototype

Spike of Databricks-like **Metric Views** on the local ledger using the community
[`semantic_views`](https://duckdb.org/community_extensions/extensions/semantic_views)
extension (`CREATE SEMANTIC VIEW` + `semantic_view(...)`).

Keeps **mean no-vig CLV** (`mean_no_vig_clv_pp`) separate from **ATS W–L**
(`ats_wins` / `ats_losses` / `ats_win_rate`). `AVG(no_vig_clv_pp)` skips NULLs.

Two equivalent ways to define `nfl_ats_process`:

| Path | Source of truth | Command |
|------|-----------------|---------|
| **SQL DDL** (reference) | `examples/semantic_views_proto.sql` | `python scripts/run_semantic_views_proto.py` |
| **YAML** (checked-in) | `metrics/nfl_ats_process.yaml` | `python scripts/run_semantic_views_proto.py --from-yaml` |

### Run in <5 minutes

```bash
# from repo root, with the project venv active (DuckDB >= 1.0; verified on 1.5.5)
export NFL_CLV_DB=./ledger.duckdb   # or your live path
# if empty: nfl-clv init && nfl-clv add --from-csv samples/week1_sample.csv
python scripts/run_semantic_views_proto.py              # SQL DDL
python scripts/run_semantic_views_proto.py --from-yaml  # YAML → CREATE … FROM YAML
```

Or run the SQL directly in the DuckDB CLI / Python:

```bash
# after INSTALL/LOAD (see examples/semantic_views_proto.sql)
duckdb "$NFL_CLV_DB" < examples/semantic_views_proto.sql
```

### YAML schema quirks (extension-native)

The extension accepts **only** dollar-quoted YAML literals:

```sql
CREATE OR REPLACE SEMANTIC VIEW nfl_ats_process FROM YAML $$
...yaml body...
$$;
```

File paths / bind parameters are **not** accepted (`FROM YAML` expects `$`). The runner
reads `metrics/nfl_ats_process.yaml` and embeds it. Round-trip via
`read_yaml_from_semantic_view('nfl_ats_process')`.

Required fields (not the same as research MetricFlow-style catalogs):

- `tables[]`: `alias`, `table`, `pk_columns`
- `dimensions[]`: `name`, `expr`, **`source_table`** (required — alias of the table)
- `metrics[]`: `name`, `expr`; **`source_table`** for table-bound metrics; omit for
  derived metrics (e.g. `ats_win_rate`)
- Optional: `joins[]`, `facts[]`, `output_type`

Illustrative research YAML under `/workspace/research/nfl-semantic-layer-guide/` uses a
different shape (`data_type` / `format` / filters without `alias`/`source_table`) and is
**docs-only** — do not copy it verbatim into this repo.

Files: `metrics/nfl_ats_process.yaml`, `examples/semantic_views_proto.sql`,
`scripts/run_semantic_views_proto.py`.
Docs: https://duckdb.org/community_extensions/extensions/semantic_views ·
https://anentropic.github.io/duckdb-semantic-views/

## License / privacy

MIT — see [`LICENSE`](LICENSE). Public repo (`WyattCurtis327/nfl-clv-ledger`). No secrets or personal bet history in the tree — keep live ledgers out of git (use `$NFL_CLV_DB` / gitignore).
