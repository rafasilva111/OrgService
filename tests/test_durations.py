import pytest

from apps.common.durations import duration_input, humanize_minutes, parse_duration


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (None, "—"),
        (0, "0min"),
        (45, "45min"),
        (60, "1h"),
        (90, "1h 30min"),
        (1440, "1d"),
        (1441, "1d 1min"),
        (1530, "1d 1h"),
        (10080, "7d"),
    ],
)
def test_humanize_minutes(minutes, expected):
    assert humanize_minutes(minutes) == expected


# Typed durations for SLAs: "1d 16h 13m" ⇄ minutes.


@pytest.mark.parametrize(
    "text, minutes",
    [
        ("1d 16h 13m", 2413),
        ("1d 16h 13min", 2413),  # what duration_input() writes back
        ("1D,4H", 1680),
        ("2h", 120),
        ("90", 90),
        ("13min", 13),
        (" 3d ", 4320),
        ("", None),
        (None, None),
        (45, 45),
    ],
)
def test_parse_duration(text, minutes):
    assert parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["abc", "1d 2d", "-5", "5x", "h", "1d and 2h", -1])
def test_parse_duration_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_duration(text)


@pytest.mark.parametrize("minutes", [0, 13, 60, 1440, 1680, 2413, 10_000])
def test_duration_input_round_trips(minutes):
    assert parse_duration(duration_input(minutes)) == minutes


def test_duration_input_blank():
    assert duration_input(None) == ""
