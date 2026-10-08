from datetime import date

import pytest

from eivlhole.config import HolidayConfig
from eivlhole.errors import ConfigError
from eivlhole.jobs import calendar

NO = HolidayConfig(country="NO", extra=["2026-12-24", "2026-07-06..2026-07-24"])


@pytest.mark.parametrize(
    "day, reason",
    [
        (date(2026, 4, 2), "Maundy Thursday"),
        (date(2026, 4, 3), "Good Friday"),
        (date(2026, 4, 6), "Easter Monday"),
        (date(2026, 5, 1), "Labor Day"),
        (date(2026, 5, 14), "Ascension Day"),
        (date(2026, 12, 25), "Christmas Day"),
    ],
)
def test_norwegian_holidays_2026(day, reason):
    d = calendar.decide(day, NO)
    assert d.skip and d.reason == reason


def test_fixed_date_holiday_on_weekday():
    d = calendar.decide(date(2028, 5, 17), NO)  # Wednesday, no overlapping holiday
    assert d.skip and d.reason == "Constitution Day"
    overlap = calendar.decide(date(2027, 5, 17), NO)  # also Pentecost Monday that year
    assert overlap.skip and "Constitution Day" in overlap.reason


def test_weekend_reason_wins_over_holiday():
    d = calendar.decide(date(2026, 5, 17), NO)  # Sunday
    assert d.skip and d.reason == "weekend (Sunday)"


def test_extras_single_and_range():
    assert calendar.decide(date(2026, 12, 24), NO) == calendar.Decision(True, "extra: 2026-12-24")
    assert calendar.decide(date(2026, 7, 15), NO).reason == "extra: 2026-07-06..2026-07-24"
    assert calendar.decide(date(2026, 7, 24), NO).skip
    assert not calendar.decide(date(2026, 7, 27), NO).skip  # Monday after the range


def test_ordinary_weekday_runs():
    assert calendar.decide(date(2026, 10, 8), NO) == calendar.Decision(False, None)


def test_country_disabled():
    cfg = HolidayConfig(country="", extra=["2026-04-03"])
    assert not calendar.decide(date(2026, 4, 2), cfg).skip  # Maundy Thursday ignored
    assert calendar.decide(date(2026, 4, 3), cfg).reason == "extra: 2026-04-03"


def test_unknown_country():
    with pytest.raises(ConfigError, match="unknown holiday country 'XX'"):
        calendar.decide(date(2026, 1, 1), HolidayConfig(country="XX"))


def test_parse_extra():
    assert calendar.parse_extra(["2026-01-02", "2026-03-01..2026-03-03"]) == [
        (date(2026, 1, 2), date(2026, 1, 2), "2026-01-02"),
        (date(2026, 3, 1), date(2026, 3, 3), "2026-03-01..2026-03-03"),
    ]


def test_skip_days_lists_weekend_holidays():
    days = dict(calendar.skip_days(2026, NO))
    assert days[date(2026, 5, 17)] == "Constitution Day"  # a Sunday, still listed
    assert days[date(2026, 12, 24)] == "extra: 2026-12-24"
    assert days[date(2026, 7, 6)] == "extra: 2026-07-06..2026-07-24"
    assert date(2026, 7, 11) not in days  # Saturday inside the range is not listed
    assert list(days) == sorted(days)
    assert date(2027, 1, 1) not in days
