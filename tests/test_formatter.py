from retitle.formatter import format_movie_filename, format_tv_filename, sanitize_filename


def test_standard_tv_format():
    result = format_tv_filename("Breaking Bad", 5, 16, "Felina", "mkv")
    assert result == "Breaking Bad - S05E16 - Felina.mkv"


def test_tv_no_episode_title():
    result = format_tv_filename("Breaking Bad", 5, 16, None, "mkv")
    assert result == "Breaking Bad - S05E16.mkv"


def test_tv_multi_episode():
    result = format_tv_filename("The Penguin", 1, [1, 2], "Pilot", "mkv")
    assert result == "The Penguin - S01E01E02 - Pilot.mkv"


def test_movie_format():
    result = format_movie_filename("Inception", 2010, "mkv")
    assert result == "Inception (2010).mkv"


def test_sanitize_colon():
    result = sanitize_filename("Chapter One: The Heir.mkv")
    assert ":" not in result
    assert "Chapter One - The Heir.mkv" == result


def test_sanitize_illegal_chars():
    result = sanitize_filename('Show "Name" <test>?.mkv')
    assert '"' not in result
    assert "<" not in result
    assert ">" not in result
    assert "?" not in result


def test_sanitize_trailing_dots():
    result = sanitize_filename("test...")
    assert not result.endswith(".")


def test_sanitize_multiple_spaces():
    result = sanitize_filename("too   many   spaces.mkv")
    assert "   " not in result
    assert result == "too many spaces.mkv"
