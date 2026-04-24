from pathlib import Path
from unittest.mock import MagicMock

import pytest

from retitle.api.musicbrainz import ReleaseDetails, ReleaseSearchResult, TrackInfo
from retitle.formatter import format_album_folder, format_track_filename
from retitle.music import (
    AudioTags,
    MusicRenamer,
    _album_from_folder,
    _match_track,
    _parse_track,
    _parse_year,
)


# ---------- formatter ----------


def test_format_track_filename_basic():
    assert format_track_filename(1, "Song", "mp3") == "01 Song.mp3"


def test_format_track_filename_double_digit_stays_two_wide():
    assert format_track_filename(12, "Song", "mp3") == "12 Song.mp3"


def test_format_track_filename_triple_digit_when_many_tracks():
    assert (
        format_track_filename(5, "Song", "mp3", total_tracks=120) == "005 Song.mp3"
    )


def test_format_track_filename_sanitizes_colon():
    result = format_track_filename(1, "Intro: Part I", "mp3")
    assert ":" not in result
    assert result == "01 Intro - Part I.mp3"


def test_format_album_folder_with_year():
    assert format_album_folder("Kid A", 2000) == "[2000] Kid A"


def test_format_album_folder_no_year():
    assert format_album_folder("Demo", None) == "Demo"


def test_format_album_folder_sanitizes():
    result = format_album_folder('Hello: World?', 2020)
    assert "?" not in result
    assert ":" not in result


# ---------- helpers ----------


def test_parse_track_plain():
    assert _parse_track("5") == (5, None)


def test_parse_track_with_total():
    assert _parse_track("3/12") == (3, 12)


def test_parse_track_zero_padded():
    assert _parse_track("03/12") == (3, 12)


def test_parse_track_none():
    assert _parse_track(None) == (None, None)


def test_parse_track_garbage():
    assert _parse_track("abc") == (None, None)


def test_parse_year_plain():
    assert _parse_year("2010") == 2010


def test_parse_year_iso_date():
    assert _parse_year("2010-05-12") == 2010


def test_parse_year_none():
    assert _parse_year(None) is None


def test_parse_year_invalid():
    assert _parse_year("nope") is None


def test_album_from_folder_with_leading_year():
    assert _album_from_folder("[2010] Kid A") == "Kid A"


def test_album_from_folder_with_trailing_year():
    assert _album_from_folder("Kid A (2010)") == "Kid A"


def test_album_from_folder_with_artist_dash():
    assert _album_from_folder("Radiohead - Kid A") == "Kid A"


def test_album_from_folder_combined():
    assert _album_from_folder("Radiohead - [2000] Kid A") == "Kid A"


def test_album_from_folder_plain():
    assert _album_from_folder("Some Album") == "Some Album"


# ---------- _match_track ----------


def _release(count: int) -> ReleaseDetails:
    return ReleaseDetails(
        release_id="id-1",
        title="Test Album",
        artist="Test Artist",
        year=2020,
        tracks=[TrackInfo(position=i, title=f"Track {i}") for i in range(1, count + 1)],
    )


def test_match_track_by_tag():
    release = _release(10)
    group = MagicMock(files=[Path(f"file{i}.mp3") for i in range(10)])
    tags = AudioTags(track=5)
    result = _match_track(Path("file4.mp3"), tags, group, release)
    assert result is not None
    assert result.position == 5


def test_match_track_by_filename_digits():
    release = _release(10)
    files = [Path(f"{i:02d} song.mp3") for i in range(1, 11)]
    group = MagicMock(files=files)
    tags = AudioTags()
    result = _match_track(files[6], tags, group, release)
    assert result is not None
    assert result.position == 7


def test_match_track_positional_when_counts_match():
    release = _release(5)
    files = [Path(f"random-{c}.mp3") for c in "abcde"]
    group = MagicMock(files=files)
    tags = AudioTags()
    result = _match_track(files[2], tags, group, release)
    assert result is not None
    assert result.position == 3


def test_match_track_none_when_counts_differ():
    release = _release(12)
    files = [Path("mystery.mp3")]
    group = MagicMock(files=files)
    tags = AudioTags()
    result = _match_track(files[0], tags, group, release)
    assert result is None


# ---------- MusicRenamer.build_album_proposal ----------


def test_build_album_proposal_ready_folder_rename():
    mb = MagicMock()
    renamer = MusicRenamer(mb)

    folder = Path("/tmp/old_folder_name")
    files = [folder / f"song{i}.mp3" for i in range(1, 4)]
    from retitle.music import AlbumGroup  # local import to keep top minimal

    group = AlbumGroup(
        folder=folder,
        files=files,
        album_hint="Kid A",
        artist_hint="Radiohead",
        year_hint=2000,
        file_tags={f: AudioTags() for f in files},
    )
    release = ReleaseDetails(
        release_id="mb-1",
        title="Kid A",
        artist="Radiohead",
        year=2000,
        tracks=[
            TrackInfo(position=1, title="Everything in Its Right Place"),
            TrackInfo(position=2, title="Kid A"),
            TrackInfo(position=3, title="The National Anthem"),
        ],
    )

    ap = renamer.build_album_proposal(group, release)

    assert ap.folder_status == "ready"
    assert ap.new_folder_name == "[2000] Kid A"
    assert len(ap.tracks) == 3
    assert all(t.status == "ready" for t in ap.tracks)
    assert ap.tracks[0].new_filename == "01 Everything in Its Right Place.mp3"
    assert ap.tracks[1].new_filename == "02 Kid A.mp3"
    assert ap.tracks[1].new_tags.album == "Kid A"
    assert ap.tracks[1].new_tags.track == 2
    assert ap.tracks[1].new_tags.track_total == 3


def test_build_album_proposal_no_match_marks_all_tracks():
    mb = MagicMock()
    renamer = MusicRenamer(mb)
    from retitle.music import AlbumGroup

    folder = Path("/tmp/unknown")
    files = [folder / "a.mp3", folder / "b.mp3"]
    group = AlbumGroup(
        folder=folder,
        files=files,
        album_hint=None,
        artist_hint=None,
        year_hint=None,
        file_tags={f: AudioTags() for f in files},
    )
    ap = renamer.build_album_proposal(group, None)
    assert ap.release is None
    assert ap.folder_status == "none"
    assert all(t.status == "no_match" for t in ap.tracks)


def test_build_album_proposal_no_folder_rename_when_disabled():
    mb = MagicMock()
    renamer = MusicRenamer(mb)
    from retitle.music import AlbumGroup

    folder = Path("/tmp/whatever")
    files = [folder / "x.mp3"]
    group = AlbumGroup(
        folder=folder,
        files=files,
        album_hint="Album",
        artist_hint="Artist",
        year_hint=2020,
        file_tags={files[0]: AudioTags()},
    )
    release = ReleaseDetails(
        release_id="id",
        title="Album",
        artist="Artist",
        year=2020,
        tracks=[TrackInfo(position=1, title="Track")],
    )
    ap = renamer.build_album_proposal(group, release, rename_folder=False)
    assert ap.folder_status == "none"
    assert ap.new_folder_name is None
