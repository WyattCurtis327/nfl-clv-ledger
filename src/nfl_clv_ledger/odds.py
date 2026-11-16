"""American odds ↔ implied probability, multiplicative no-vig, and CLV helpers.

No-vig method (v1): **multiplicative**.
For a two-way market with raw implied probabilities p_a, p_b:
    no_vig_a = p_a / (p_a + p_b)

When only one side's American odds are logged (typical ATS row), we synthesize
the opposite at **-110** (standard ATS juice). Documented in README.

CLV (probability points), sign aligned with Concepts glossary:
    no_vig_clv_pp = 100 * (close_no_vig_prob - bet_no_vig_prob)

Positive → you bought cheaper no-vig probability than the fair close (beat the
close). Negative → worse number than close. Process quality is judged by the
distribution of these values over a season — never by W-L alone.

Point-adjusted CLV adds a spread-move term in the same units:
    clv_adj_pp = no_vig_clv_pp + point_value_pp(bet_spread, close_spread)

`point_value_pp` walks every 0.5-point step between the signed spreads and sums
P(margin = n) / 2 for the integer key of each step (see NFL_PUSH_PP). The sign
follows point_move = bet_spread - close_spread (positive when we beat the
number). Documented in README Math notes.

Break-even at −110 ≈ 52.38% is a reference only (raw implied, before no-vig).
"""

from __future__ import annotations

from typing import Iterator, Optional

# Standard ATS juice used when opposite side is not supplied.
DEFAULT_OPPOSITE_AMERICAN = -110.0

# Raw implied probability of -110 (reference break-even for a juiced ATS side).
BREAKEVEN_MINUS_110 = 110.0 / 210.0  # ≈ 0.5238095

# Simple NFL key-number push-probability table, in probability points
# (100 × P(final margin equals n)). Heavier weight on 3, 7, 6, 10, 14.
NFL_PUSH_PP: dict[int, float] = {
    0: 0.3,
    1: 3.3,
    2: 3.5,
    3: 15.0,
    4: 5.2,
    5: 3.1,
    6: 6.4,
    7: 9.4,
    8: 3.2,
    9: 2.1,
    10: 6.2,
    11: 2.0,
    12: 1.5,
    13: 2.4,
    14: 5.4,
    15: 1.3,
    16: 1.6,
    17: 3.5,
    18: 1.0,
    19: 0.8,
    20: 2.0,
    21: 2.8,
    22: 0.6,
    23: 0.7,
    24: 2.0,
    25: 0.5,
    26: 0.6,
    27: 1.2,
    28: 1.5,
}

# Used for any integer |n| not in NFL_PUSH_PP.
DEFAULT_PUSH_PP = 0.4


def american_to_implied(american: float) -> float:
    """Convert American odds to raw (vigged) implied probability in (0, 1)."""
    a = float(american)
    if a >= 100:
        return 100.0 / (a + 100.0)
    if a <= -100:
        return abs(a) / (abs(a) + 100.0)
    raise ValueError(f"American odds must be <= -100 or >= +100, got {american}")


def implied_to_american(prob: float) -> float:
    """Convert implied probability in (0, 1) to American odds (rounded to 0.01)."""
    p = float(prob)
    if not 0.0 < p < 1.0:
        raise ValueError(f"Probability must be in (0, 1), got {prob}")
    if p < 0.5:
        return round(100.0 * (1.0 - p) / p, 2)
    return round(-100.0 * p / (1.0 - p), 2)


def multiplicative_novig(prob_a: float, prob_b: float) -> tuple[float, float]:
    """Multiplicative no-vig: normalize a two-way pair to sum to 1."""
    pa, pb = float(prob_a), float(prob_b)
    if pa <= 0 or pb <= 0:
        raise ValueError("Implied probabilities must be positive")
    total = pa + pb
    return pa / total, pb / total


def no_vig_prob(
    american: float,
    opposite_american: Optional[float] = None,
) -> float:
    """No-vig win probability for `american` via multiplicative method.

    If `opposite_american` is omitted, assume the other side is priced at -110
    (ATS default juice).
    """
    opp = (
        DEFAULT_OPPOSITE_AMERICAN
        if opposite_american is None
        else float(opposite_american)
    )
    p_side = american_to_implied(american)
    p_opp = american_to_implied(opp)
    nv_side, _ = multiplicative_novig(p_side, p_opp)
    return nv_side


def normalize_american_price(price: Optional[float], *, assume_minus_110: bool = True) -> Optional[float]:
    """Coerce a logged price to American odds.

    - None stays None.
    - Values already in American form (<= -100 or >= 100) pass through.
    - If juice is omitted / not a valid American and assume_minus_110, return -110.
    """
    if price is None:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return DEFAULT_OPPOSITE_AMERICAN if assume_minus_110 else None
    if p <= -100 or p >= 100:
        return p
    # Not a valid American quote (e.g. bare spread like -3.5 logged by mistake).
    if assume_minus_110:
        return DEFAULT_OPPOSITE_AMERICAN
    return None


def no_vig_clv_pp(
    bet_american: float,
    close_american: float,
    *,
    bet_opposite: Optional[float] = None,
    close_opposite: Optional[float] = None,
) -> float:
    """CLV in probability points: 100 * (close_nv - bet_nv). Positive = beat close."""
    bet_nv = no_vig_prob(bet_american, bet_opposite)
    close_nv = no_vig_prob(close_american, close_opposite)
    return round(100.0 * (close_nv - bet_nv), 6)


def compute_clv_if_possible(
    bet_price: Optional[float],
    close_price: Optional[float],
) -> Optional[float]:
    """Return no_vig_clv_pp when both bet and close American prices are present."""
    bet = normalize_american_price(bet_price)
    close = normalize_american_price(close_price)
    if bet is None or close is None:
        return None
    return no_vig_clv_pp(bet, close)


def compute_point_move(
    bet_spread: Optional[float],
    close_spread: Optional[float],
) -> Optional[float]:
    """Signed spread change: bet_spread - close_spread. Positive = beat the number.

    Example: bet -10.5, close -13.5 → +3.0.
    """
    if bet_spread is None or close_spread is None:
        return None
    return round(float(bet_spread) - float(close_spread), 6)


def _is_integer_spread(value: float) -> bool:
    return abs(value - round(value)) < 1e-9


def _half_step_key(start: float, end: float) -> int:
    """Integer margin key for a 0.5-point step (one endpoint is always an integer)."""
    for value in (start, end):
        if _is_integer_spread(value):
            return int(round(abs(value)))
    raise ValueError(f"half-step {start} -> {end} has no integer endpoint")


def _iter_half_steps(start: float, end: float) -> Iterator[tuple[float, float]]:
    """Yield successive 0.5-point steps from start to end, avoiding float drift."""
    start_h = int(round(float(start) * 2))
    end_h = int(round(float(end) * 2))
    if start_h == end_h:
        return
    direction = 1 if end_h > start_h else -1
    cur = start_h
    while cur != end_h:
        nxt = cur + direction
        yield (cur / 2.0, nxt / 2.0)
        cur = nxt


def push_pp_for_margin(n: int) -> float:
    """Push probability in pp for integer margin |n|."""
    return NFL_PUSH_PP.get(abs(int(n)), DEFAULT_PUSH_PP)


def point_value_pp(
    bet_spread: Optional[float],
    close_spread: Optional[float],
) -> Optional[float]:
    """Convert a signed spread move into probability points via NFL_PUSH_PP.

    Each 0.5 step contributes P(n) / 2 for its integer key n, so fully crossing
    n (win-on-n → loss-on-n) sums to P(n) once — the same cover-probability
    units as no_vig_clv_pp. The sign follows point_move (positive when we beat
    the number). Returns None if either spread is missing.
    """
    if bet_spread is None or close_spread is None:
        return None
    bet = float(bet_spread)
    close = float(close_spread)
    move = bet - close
    if abs(move) < 1e-12:
        return 0.0
    total = 0.0
    for a, b in _iter_half_steps(bet, close):
        total += push_pp_for_margin(_half_step_key(a, b)) / 2.0
    signed = total if move > 0 else -total
    return round(signed, 6)


def compute_clv_adj_pp(
    bet_price: Optional[float],
    close_price: Optional[float],
    bet_spread: Optional[float],
    close_spread: Optional[float],
) -> Optional[float]:
    """Price CLV plus the point-value term, or None if either piece is missing.

    clv_adj_pp stays NULL unless both spreads and both juices are present.
    It does not silently fall back to price-only CLV.
    """
    price = compute_clv_if_possible(bet_price, close_price)
    point = point_value_pp(bet_spread, close_spread)
    if price is None or point is None:
        return None
    return round(price + point, 6)
