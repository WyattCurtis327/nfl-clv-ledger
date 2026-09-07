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
nfl-clv add …                # log a pick (or --from-csv)
nfl-clv set-close …          # fill closes / Monday catch-up
nfl-clv grade --week N       # week + season CLV and ATS W–L (separate)
nfl-clv export --week N -o … # CSV for Analytics workspace
nfl-clv summary              # season cumulative CLV vs W–L
```

Default DB path: env `NFL_CLV_DB` (suggested `/workspace/nfl-analytics/clv/ledger.duckdb`).

## Schema (skill-aligned)

`season | week | game_id | side | market | book | open_price | bet_price | close_price | open_ts | bet_ts | close_ts | no_vig_clv_pp | result | notes`

`market`: `ats` | `ml`. `result`: win / loss / push / pending / no_bet.

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
CLV with ATS W–L.

## Install / test

```bash
pip install -e ".[dev]"
pytest
```

## License / privacy

Private repo (`WyattCurtis327/nfl-clv-ledger`). No secrets in the tree.
