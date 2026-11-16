"""Unit tests for American odds / no-vig / CLV helpers."""

from __future__ import annotations

import pytest

from nfl_clv_ledger.odds import (
    BREAKEVEN_MINUS_110,
    DEFAULT_PUSH_PP,
    NFL_PUSH_PP,
    american_to_implied,
    compute_clv_adj_pp,
    compute_point_move,
    implied_to_american,
    multiplicative_novig,
    no_vig_clv_pp,
    no_vig_prob,
    point_value_pp,
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


def test_key_table_heavier_on_requested_numbers():
    for key in (3, 7, 6, 10, 14):
        assert NFL_PUSH_PP[key] > DEFAULT_PUSH_PP
    assert NFL_PUSH_PP[3] == 15.0
    assert NFL_PUSH_PP[7] == 9.4
    assert NFL_PUSH_PP[6] == 6.4
    assert NFL_PUSH_PP[10] == 6.2
    assert NFL_PUSH_PP[14] == 5.4
    assert NFL_PUSH_PP[3] > NFL_PUSH_PP[7] > NFL_PUSH_PP[6] > NFL_PUSH_PP[10] > NFL_PUSH_PP[14]


def test_point_move_favorite_example():
    # Bet -10.5, close -13.5 → we beat the number by 3.
    assert compute_point_move(-10.5, -13.5) == 3.0


def test_point_value_worked_examples():
    assert point_value_pp(-10.5, -13.5) == pytest.approx(5.9)
    assert point_value_pp(10.5, 7.5) == pytest.approx(11.5)
    assert point_value_pp(-3.0, -3.5) == pytest.approx(7.5)
    assert point_value_pp(-3.5, -3.0) == pytest.approx(-7.5)
    assert point_value_pp(-7.0, -7.0) == pytest.approx(0.0)


def test_point_value_null_if_spread_missing():
    assert point_value_pp(None, -13.5) is None
    assert point_value_pp(-10.5, None) is None
    assert compute_point_move(None, -13.5) is None


def test_clv_adj_null_unless_price_and_spreads_present():
    # Spreads missing → not a silent price-only fallback.
    price_only = no_vig_clv_pp(-110, -120)
    assert price_only != 0
    assert compute_clv_adj_pp(-110, -120, None, None) is None
    assert compute_clv_adj_pp(-110, -120, -10.5, None) is None
    assert compute_clv_adj_pp(None, -120, -10.5, -13.5) is None
    adj = compute_clv_adj_pp(-110, -120, -10.5, -13.5)
    assert adj == pytest.approx(price_only + 5.9)


def test_clv_adj_does_not_change_price_only_function():
    # The juice-only helper is independent of spreads.
    assert no_vig_clv_pp(-110, -120) == 1.012146
