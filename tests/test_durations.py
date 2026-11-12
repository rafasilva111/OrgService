import pytest

from apps.common.durations import humanize_minutes


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
