import pytest

from bakehouse_core.transforms.promo import break_even_uplift, project_revenue


def test_no_volume_change_is_just_the_discount():
    assert project_revenue([100, 200], 0.10) == pytest.approx([90, 180])


def test_volume_uplift_scales_on_top_of_the_discount():
    assert project_revenue([100], 0.10, 0.05) == pytest.approx([94.5])


def test_no_discount_and_no_uplift_changes_nothing():
    assert project_revenue([100, 50], 0) == [100, 50]


def test_break_even_uplift_recovers_the_original_revenue():
    rate = 0.20
    uplift = break_even_uplift(rate)
    assert uplift == pytest.approx(0.25)
    assert project_revenue([100], rate, uplift) == pytest.approx([100])


def test_discount_outside_the_parameter_range_is_rejected():
    for bad in (-0.01, 0.51):
        with pytest.raises(ValueError, match="discount_rate"):
            project_revenue([1], bad)
        with pytest.raises(ValueError, match="discount_rate"):
            break_even_uplift(bad)


def test_losing_all_volume_is_rejected():
    with pytest.raises(ValueError, match="volume_uplift"):
        project_revenue([1], 0.1, -1)
