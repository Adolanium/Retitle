import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from retitle.api.tvmaze import EpisodeLookupResult, TVMazeClient
from retitle.renamer import Renamer


def _make_mock_tvmaze() -> TVMazeClient:
    mock = MagicMock(spec=TVMazeClient)
    mock.get_episode_title.return_value = EpisodeLookupResult(
        show_name="A Knight of the Seven Kingdoms",
        episode_title="The Hedge Knight",
        season=1,
        episode=1,
        air_date="2025-06-22",
    )
    return mock


def test_propose_tv_rename():
    tvmaze = _make_mock_tvmaze()
    renamer = Renamer(tvmaze)

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "A.Knight.of.the.Seven.Kingdoms.S01E01.1080p.x265-ELiTE.mkv"
        filepath.touch()

        proposal = renamer.propose_rename(filepath)
        assert proposal.status == "ready"
        assert proposal.new_filename == "A Knight of the Seven Kingdoms - S01E01 - The Hedge Knight.mkv"


def test_propose_no_api_match():
    tvmaze = MagicMock(spec=TVMazeClient)
    tvmaze.get_episode_title.return_value = None
    renamer = Renamer(tvmaze)

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "Some.Show.S01E01.720p.mkv"
        filepath.touch()

        proposal = renamer.propose_rename(filepath)
        # Should still propose a rename using parsed title, just no episode title
        assert proposal.status == "ready"
        assert "S01E01" in proposal.new_filename


def test_execute_rename():
    tvmaze = _make_mock_tvmaze()
    renamer = Renamer(tvmaze)

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "A.Knight.of.the.Seven.Kingdoms.S01E01.1080p.x265-ELiTE.mkv"
        filepath.touch()

        proposal = renamer.propose_rename(filepath)
        assert proposal.status == "ready"

        success = renamer.execute_rename(proposal)
        assert success
        assert proposal.new_path.exists()
        assert not filepath.exists()


def test_conflict_detection():
    tvmaze = _make_mock_tvmaze()
    renamer = Renamer(tvmaze)

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "A.Knight.of.the.Seven.Kingdoms.S01E01.1080p.x265-ELiTE.mkv"
        filepath.touch()
        # Create the target file to cause a conflict
        conflict = Path(tmpdir) / "A Knight of the Seven Kingdoms - S01E01 - The Hedge Knight.mkv"
        conflict.touch()

        proposal = renamer.propose_rename(filepath)
        assert proposal.status == "conflict"


def test_batch_scanning():
    tvmaze = _make_mock_tvmaze()
    renamer = Renamer(tvmaze)

    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "show.S01E01.mkv").touch()
        (Path(tmpdir) / "show.S01E02.mkv").touch()
        (Path(tmpdir) / "readme.txt").touch()  # Not a media file

        proposals = renamer.propose_batch(Path(tmpdir))
        assert len(proposals) == 2  # Only .mkv files


def test_low_confidence_skipped():
    tvmaze = MagicMock(spec=TVMazeClient)
    renamer = Renamer(tvmaze)

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "S01E05.mkv"
        filepath.touch()

        proposal = renamer.propose_rename(filepath)
        assert proposal.status == "no_match"
        tvmaze.get_episode_title.assert_not_called()
