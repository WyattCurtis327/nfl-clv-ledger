# Design note — nfl-clv-ledger

## Purpose

Append-only DuckDB ledger + CLI so NFL Analytics can report **no-vig closing-line value (CLV)** as a process score, separately from ATS W–L.

## Storage

Single DuckDB file via `$NFL_CLV_DB` (default `./ledger.duckdb`, cwd-relative). Optional agent-box path: `NFL_CLV_DB=/workspace/nfl-analytics/clv/ledger.duckdb`. Table `ledger` holds one row per intended bet (including explicit `no_bet` weeks).

## Price convention (ATS v1)

`open_price` / `bet_price` / `close_price` store **American odds on the chosen side** (juice), e.g. `-110`. Spreads themselves are not the price fields — put the side/spread context in `side` / `notes` / `game_id` as needed.

If juice is omitted, v1 **assumes −110**.

## No-vig

**Multiplicative** two-way normalization. Missing opposite side → synthesize opposite at −110. See README Math notes.

## CLV sign

`no_vig_clv_pp = 100 × (close_nv − bet_nv)`. Positive = beat the close (cheaper no-vig probability). Matches Concepts glossary.

## Boundaries

- No book API keys in the repo.
- Summary / grade always print season CLV and ATS W–L on **separate lines**.
- Process ≠ profitability; never claim “beats Vegas” from W–L alone.
