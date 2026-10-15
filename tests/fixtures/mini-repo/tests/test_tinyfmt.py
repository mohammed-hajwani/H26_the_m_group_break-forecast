from tinyfmt.cli import run
from tinyfmt.dates import known_zone, to_utc
from tinyfmt.text import ensure_text, title_words


def test_to_utc_kolkata():
    assert to_utc("2024-01-15 10:30", "Asia/Kolkata") == "2024-01-15T05:00:00+00:00"


def test_known_zone():
    assert known_zone("Europe/Paris") and not known_zone("Mars/Olympus")


def test_ensure_text_bytes():
    assert ensure_text(b"caf\xc3\xa9") == "café"


def test_title_words():
    assert title_words("  hello   brave world ") == "Hello Brave World"


def test_cli_title():
    assert run(["title", "dependency exit"]) == "Dependency Exit"


def test_cli_utc():
    assert run(["utc", "2024-07-01 12:00", "Europe/London"]) == "2024-07-01T11:00:00+00:00"
