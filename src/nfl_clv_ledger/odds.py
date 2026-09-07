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

Break-even at −110 ≈ 52.38% is a reference only (raw implied, before no-vig).
"""

from __future__ import annotations

from typing import Optional

# Standard ATS juice used when opposite side is not supplied.
DEFAULT_OPPOSITE_AMERICAN = -110.0

# Raw implied probability of -110 (reference break-even for a juiced ATS side).
BREAKEVEN_MINUS_110 = 110.0 / 210.0  # ≈ 0.5238095


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
