"""Unit tests for American odds / no-vig / CLV helpers."""

from __future__ import annotations

import pytest

from nfl_clv_ledger.odds import (
    BREAKEVEN_MINUS_110,
    american_to_implied,
    implied_to_american,
    multiplicative_novig,
    no_vig_clv_pp,
    no_vig_prob,
)


def test_american_to_implied_minus_110():
    p = american_to_implied(-110)
    assert abs(p - BREAKEVEN_MINUS_110) < 1e-9
    assert abs(p - 0.5238095238) < 1e-9


def test_american_to_implied_plus_100():
    assert abs(american_to_implied(100) - 0.5) < 1e-12


def test_american_roundtrip_favorite():
    a = -150.0
    p = american_to_implied(a)
    assert abs(implied_to_american(p) - a) < 0.05


def test_american_roundtrip_dog():
    a = 130.0
    p = american_to_implied(a)
    assert abs(implied_to_american(p) - a) < 0.05


def test_multiplicative_novig_symmetric():
    p = american_to_implied(-110)
    a, b = multiplicative_novig(p, p)
    assert abs(a - 0.5) < 1e-12
    assert abs(b - 0.5) < 1e-12


def test_no_vig_prob_default_opposite_minus_110():
    assert abs(no_vig_prob(-110) - 0.5) < 1e-12


def test_no_vig_clv_positive_when_beat_close():
    # Bet -110 (nv 0.5), close -120 (higher nv) → positive CLV (beat close)
    clv = no_vig_clv_pp(-110, -120)
    assert clv > 0


def test_no_vig_clv_negative_when_worse_than_close():
    clv = no_vig_clv_pp(-120, -110)
    assert clv < 0


def test_invalid_american():
    with pytest.raises(ValueError):
        american_to_implied(0)
    with pytest.raises(ValueError):
        american_to_implied(-50)
