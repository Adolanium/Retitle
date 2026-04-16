import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from retitle.api.opensubtitles import (
    OpenSubtitlesClient,
    SubtitleDownloadResult,
    SubtitleSearchResult,
)
from retitle.parser import parse_filename
from retitle.subtitles import SubtitleDownloader


def _make_mock_client() -> OpenSubtitlesClient:
    mock = MagicMock(spec=OpenSubtitlesClient)
    mock.search.return_value = [
        SubtitleSearchResult(
            file_id=101,
            language="en",
            release="Breaking.Bad.S01E01.1080p",
            download_count=5000,
            from_trusted=True,
        ),
        SubtitleSearchResult(
            file_id=102,
            language="en",
            release="Breaking.Bad.S01E01.720p",
            download_count=2000,
            from_trusted=False,
        ),
    ]
    mock.download.return_value = SubtitleDownloadResult(
        download_url="https://example.com/sub.srt",
        file_name="subtitle.srt",
        remaining=19,
    )
    mock.download_content.return_value = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
    return mock


def test_propose_subtitle_for_tv():
    client = _make_mock_client()
    downloader = SubtitleDownloader(client, language="en")

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "Breaking.Bad.S01E01.1080p.mkv"
        filepath.touch()

        parsed = parse_filename(filepath.name)
        proposal = downloader.propose_subtitle(filepath, parsed)

        assert proposal.status == "found"
        assert proposal.selected_result is not None
        assert proposal.selected_result.file_id == 101
        assert len(proposal.search_results) == 2


def test_propose_subtitle_for_movie():
    client = _make_mock_client()
    client.search.return_value = [
        SubtitleSearchResult(
            file_id=201,
            language="en",
            release="Inception.2010.1080p",
            download_count=10000,
            from_trusted=True,
        ),
    ]
    downloader = SubtitleDownloader(client, language="en")

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "Inception.2010.BluRay.mkv"
        filepath.touch()

        parsed = parse_filename(filepath.name)
        proposal = downloader.propose_subtitle(filepath, parsed)

        assert proposal.status == "found"
        assert proposal.selected_result.file_id == 201


def test_subtitle_path_generation():
    client = _make_mock_client()
    downloader = SubtitleDownloader(client, language="fr")

    media_path = Path("/tmp/Movie (2024).mkv")
    subtitle_path = downloader._build_subtitle_path(media_path)
    assert subtitle_path == Path("/tmp/Movie (2024).fr.srt")


def test_subtitle_path_generation_english():
    client = _make_mock_client()
    downloader = SubtitleDownloader(client, language="en")

    media_path = Path("/tmp/Show - S01E01 - Pilot.mkv")
    subtitle_path = downloader._build_subtitle_path(media_path)
    assert subtitle_path == Path("/tmp/Show - S01E01 - Pilot.en.srt")


def test_not_found_status():
    client = _make_mock_client()
    client.search.return_value = []
    downloader = SubtitleDownloader(client, language="en")

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "Some.Show.S01E01.mkv"
        filepath.touch()

        parsed = parse_filename(filepath.name)
        proposal = downloader.propose_subtitle(filepath, parsed)

        assert proposal.status == "not_found"
        assert proposal.selected_result is None


def test_skip_when_subtitle_exists():
    client = _make_mock_client()
    downloader = SubtitleDownloader(client, language="en")

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "Some.Show.S01E01.mkv"
        filepath.touch()
        # Create existing subtitle
        subtitle = Path(tmpdir) / "Some.Show.S01E01.en.srt"
        subtitle.touch()

        parsed = parse_filename(filepath.name)
        proposal = downloader.propose_subtitle(filepath, parsed)

        assert proposal.status == "skipped"
        assert proposal.error_message == "Subtitle already exists"
        client.search.assert_not_called()


def test_execute_download_saves_file():
    client = _make_mock_client()
    downloader = SubtitleDownloader(client, language="en")

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "Breaking.Bad.S01E01.mkv"
        filepath.touch()

        parsed = parse_filename(filepath.name)
        proposal = downloader.propose_subtitle(filepath, parsed)
        assert proposal.status == "found"

        success = downloader.execute_download(proposal)
        assert success
        assert proposal.status == "downloaded"
        assert proposal.subtitle_path.exists()
        assert proposal.subtitle_path.read_bytes() == b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"


def test_execute_download_skips_not_found():
    client = _make_mock_client()
    client.search.return_value = []
    downloader = SubtitleDownloader(client, language="en")

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "Some.Show.S01E01.mkv"
        filepath.touch()

        parsed = parse_filename(filepath.name)
        proposal = downloader.propose_subtitle(filepath, parsed)
        assert proposal.status == "not_found"

        success = downloader.execute_download(proposal)
        assert not success


def test_batch_scanning():
    client = _make_mock_client()
    downloader = SubtitleDownloader(client, language="en")

    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "show.S01E01.mkv").touch()
        (Path(tmpdir) / "show.S01E02.mkv").touch()
        (Path(tmpdir) / "readme.txt").touch()  # Not a media file

        proposals = downloader.propose_batch(Path(tmpdir))
        assert len(proposals) == 2  # Only .mkv files
