"""Hidden oracle tests for humanize.

Never shown to SentinelPR. The benchmark runs them, with the base revision's own tests,
against every generated PR to label it by execution. They pin the exact boundaries the
public suite leaves open (the byte, minute, hour and millisecond cut-offs, clamping at the
limit, the switch to scientific notation), so a PR can pass every visible test and still be
caught here. Expected values are the behaviour of the released versions used as bases.
"""

from __future__ import annotations

import datetime as dt

import pytest

import humanize
from humanize.time import Unit

T = dt.timedelta


class TestNaturalsize:
    @pytest.mark.parametrize(
        ("value", "kwargs", "expected"),
        [
            (1, {}, "1 Byte"),
            (999, {}, "999 Bytes"),
            (1000, {}, "1.0 kB"),
            (999_999, {}, "1000.0 kB"),
            (10**6, {}, "1.0 MB"),
            (10**9, {}, "1.0 GB"),
            (-1000, {}, "-1.0 kB"),
            (1023, {"binary": True}, "1023 Bytes"),
            (1024, {"binary": True}, "1.0 KiB"),
            (1024**2, {"binary": True}, "1.0 MiB"),
            (1023, {"gnu": True}, "1023B"),
            (1024, {"gnu": True}, "1.0K"),
            (1024**2, {"gnu": True}, "1.0M"),
        ],
    )
    def test_unit_boundaries(self, value, kwargs, expected):
        assert humanize.naturalsize(value, **kwargs) == expected


class TestNumbers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(1000, "1.0 thousand"), (999_999, "1.0 million"), (10**6, "1.0 million"),
         (10**9, "1.0 billion"), (10**12, "1.0 trillion"), (-(10**6), "-1.0 million")],
    )
    def test_intword_at_powers(self, value, expected):
        assert humanize.intword(value) == expected

    def test_fractional_denominator_limit(self):
        assert humanize.fractional(1 / 1001) == "1/1000"
        assert humanize.fractional(1 / 999) == "1/999"

    def test_clamp_at_the_limit_is_not_flagged(self):
        assert humanize.clamp(1, floor=1) == "1"
        assert humanize.clamp(1, ceil=1) == "1"
        assert humanize.clamp(0.5, floor=1) == "<1"
        assert humanize.clamp(2, ceil=1) == ">1"

    def test_metric_switches_to_scientific_outside_prefixes(self):
        assert humanize.metric(1e32) == "100 Q"
        assert humanize.metric(1e-30) == "1.00 q"
        assert humanize.metric(1e33) == humanize.scientific(1e33, 2)
        assert humanize.metric(1e-31) == humanize.scientific(1e-31, 2)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_not_finite(self, value):
        expected = {"nan": "NaN", "inf": "+Inf", "-inf": "-Inf"}[str(value)]
        assert humanize.intcomma(value) == expected
        assert humanize.ordinal(value) == expected
        assert humanize.fractional(value) == expected


class TestNaturaldelta:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(59, "59 seconds"), (60, "a minute"), (119, "a minute"), (120, "2 minutes"),
         (3599, "59 minutes"), (3600, "an hour"), (7199, "an hour"), (7200, "2 hours")],
    )
    def test_second_minute_hour_cutoffs(self, seconds, expected):
        assert humanize.naturaldelta(T(seconds=seconds)) == expected

    @pytest.mark.parametrize(
        ("micros", "expected"),
        [(999, "999 microseconds"), (1000, "1 millisecond"), (999_999, "999 milliseconds")],
    )
    def test_sub_second_cutoffs(self, micros, expected):
        assert humanize.naturaldelta(T(microseconds=micros), minimum_unit="microseconds") == expected

    @pytest.mark.parametrize(
        ("days", "expected"),
        [(365, "a year"), (396, "1 year, 1 month"), (400, "1 year, 1 month"), (730, "2 years"), (1100, "3 years")],
    )
    def test_years(self, days, expected):
        assert humanize.naturaldelta(T(days=days)) == expected


class TestPrecisedelta:
    @pytest.mark.parametrize(
        ("delta", "kwargs", "expected"),
        [
            (T(days=1, hours=12), {"minimum_unit": "days"}, "1.50 days"),
            (T(days=400), {"minimum_unit": "years"}, "1.10 years"),
            (T(days=40, hours=6), {"minimum_unit": "months"}, "1.31 months"),
            (T(seconds=90, microseconds=500_000), {"minimum_unit": "seconds"}, "1 minute and 30.50 seconds"),
            (T(seconds=1, microseconds=500), {"minimum_unit": "seconds"}, "1.00 seconds"),
            (T(seconds=1, microseconds=500), {"minimum_unit": "milliseconds"}, "1 second and 0.50 milliseconds"),
            (T(seconds=1.5), {"minimum_unit": "seconds"}, "1.50 seconds"),
            (T(hours=5, minutes=3), {}, "5 hours and 3 minutes"),
            (T(hours=1, seconds=1), {}, "1 hour and 1 second"),
            (T(seconds=3600), {}, "1 hour"),
            (T(hours=3, seconds=30), {"minimum_unit": "minutes"}, "3 hours and 0.50 minutes"),
            (T(days=2, hours=5), {"suppress": ["days"]}, "53 hours"),
            (T(days=1), {"minimum_unit": "hours", "suppress": ["hours"]}, "1 day"),
        ],
    )
    def test_values(self, delta, kwargs, expected):
        assert humanize.precisedelta(delta, **kwargs) == expected

    def test_unit_ordering_is_strict(self):
        assert not Unit.SECONDS < Unit.SECONDS
        assert Unit.SECONDS < Unit.MINUTES
        assert sorted([Unit.YEARS, Unit.SECONDS, Unit.DAYS]) == [Unit.SECONDS, Unit.DAYS, Unit.YEARS]
