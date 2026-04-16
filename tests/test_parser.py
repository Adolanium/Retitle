from retitle.parser import parse_filename


def test_standard_tv_episode():
    result = parse_filename("A.Knight.of.the.Seven.Kingdoms.S01E01.1080p.x265-ELiTE.mkv")
    assert result.media_type == "episode"
    assert result.season == 1
    assert result.episode == 1
    assert result.extension == "mkv"
    assert result.confidence == "high"
    assert result.title is not None
    # Title should contain the key words
    assert "knight" in result.title.lower()
    assert "seven" in result.title.lower()


def test_standard_movie():
    result = parse_filename("Inception.2010.1080p.BluRay.x264.mkv")
    assert result.media_type == "movie"
    assert result.year == 2010
    assert result.title is not None
    assert "inception" in result.title.lower()
    assert result.confidence == "high"


def test_multi_episode():
    result = parse_filename("The.Penguin.S01E01-E02.1080p.mkv")
    assert result.media_type == "episode"
    assert result.season == 1
    assert isinstance(result.episode, list)
    assert 1 in result.episode
    assert 2 in result.episode


def test_no_title():
    result = parse_filename("S01E05.mkv")
    assert result.media_type == "episode"
    assert result.season == 1
    assert result.episode == 5
    assert result.confidence == "low"


def test_movie_no_year():
    result = parse_filename("Some.Random.Movie.1080p.mkv")
    assert result.title is not None
    assert result.confidence == "medium"


def test_breaking_bad():
    result = parse_filename("Breaking.Bad.S05E16.Felina.720p.BluRay.x264.mkv")
    assert result.media_type == "episode"
    assert result.season == 5
    assert result.episode == 16
    assert result.confidence == "high"
    assert "breaking" in result.title.lower()


def test_extension_extraction():
    result = parse_filename("show.S01E01.mp4")
    assert result.extension == "mp4"

    result = parse_filename("show.S01E01.avi")
    assert result.extension == "avi"
