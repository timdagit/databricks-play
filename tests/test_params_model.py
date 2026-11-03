from decimal import Decimal

import pytest

from bakehouse_core.params import model


def test_there_are_ten_unique_parameters():
    assert len(model.DEFINITIONS) == 10
    assert len(model.BY_KEY) == 10


@pytest.mark.parametrize("definition", model.DEFINITIONS, ids=lambda d: d.key)
def test_defaults_are_valid_and_round_trip(definition):
    assert definition.parse(definition.default) == definition.default
    text = model.to_text(definition.default)
    assert model.from_text(definition.key, text) == definition.default


def test_default_silver_is_below_gold():
    values = model.defaults()
    assert values["loyalty_silver_min_spend"] < values["loyalty_gold_min_spend"]


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("loyalty_lookback_days", "30", 30),
        ("loyalty_lookback_days", " 30 ", 30),
        ("loyalty_lookback_days", 30.0, 30),
        ("underperform_pct", "0.25", Decimal("0.25")),
        ("sales_target_uplift_pct", "-0.1", Decimal("-0.1")),
    ],
)
def test_parse_accepts(key, raw, expected):
    assert model.BY_KEY[key].parse(raw) == expected


@pytest.mark.parametrize(
    ("key", "raw", "message"),
    [
        ("loyalty_lookback_days", "abc", "number"),
        ("loyalty_lookback_days", "30.5", "whole"),
        ("loyalty_lookback_days", "6", "between"),
        ("loyalty_lookback_days", "366", "between"),
        ("underperform_pct", "0.04", "between"),
        ("underperform_pct", "NaN", "finite"),
        ("underperform_pct", "inf", "finite"),
        ("promo_discount_rate", "", "number"),
    ],
)
def test_parse_rejects(key, raw, message):
    with pytest.raises(ValueError, match=message):
        model.BY_KEY[key].parse(raw)


def test_valid_changes_pass():
    parsed, errors = model.validate_changes(model.defaults(), {"min_txn_for_ranking": "75"})
    assert errors == {}
    assert parsed == {"min_txn_for_ranking": 75}


def test_unknown_key_is_an_error():
    _, errors = model.validate_changes(model.defaults(), {"nope": "1"})
    assert errors == {"nope": "unknown parameter"}


def test_silver_must_stay_below_gold_when_silver_raised():
    _, errors = model.validate_changes(model.defaults(), {"loyalty_silver_min_spend": "250"})
    assert "loyalty_silver_min_spend" in errors


def test_silver_must_stay_below_gold_when_gold_lowered():
    _, errors = model.validate_changes(model.defaults(), {"loyalty_gold_min_spend": "100"})
    assert "loyalty_silver_min_spend" in errors


def test_raising_both_together_is_fine():
    changes = {"loyalty_silver_min_spend": "400", "loyalty_gold_min_spend": "800"}
    _, errors = model.validate_changes(model.defaults(), changes)
    assert errors == {}


def test_range_error_does_not_also_report_cross_field():
    _, errors = model.validate_changes(model.defaults(), {"loyalty_silver_min_spend": "-1"})
    assert list(errors) == ["loyalty_silver_min_spend"]
    assert "between" in errors["loyalty_silver_min_spend"]
