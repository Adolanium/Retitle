"""Music rename — read ID3/tag metadata, look up MusicBrainz, rename files/folders."""
import dataclasses
import re
from pathlib import Path

import mutagen
import requests

from retitle.api.musicbrainz import (
    MusicBrainzClient,
    ReleaseDetails,
    ReleaseSearchResult,
    TrackInfo,
)
from retitle.formatter import format_album_folder, format_track_filename

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".wma", ".wav"}

# Mutagen Easy tag keys → our AudioTags fields.
_EASY_KEYS = {
    "title": "title",
    "artist": "artist",
    "albumartist": "albumartist",
    "album": "album",
    "tracknumber": "track_raw",
    "date": "date_raw",
    "year": "date_raw",
    "genre": "genre",
}


@dataclasses.dataclass
class AudioTags:
    title: str | None = None
    artist: str | None = None
    albumartist: str | None = None
    album: str | None = None
    track: int | None = None
    track_total: int | None = None
    year: int | None = None
    genre: str | None = None


@dataclasses.dataclass
class AlbumGroup:
    """A set of audio files that belong to the same album (grouped by folder)."""

    folder: Path
    files: list[Path]
    album_hint: str | None
    artist_hint: str | None
    year_hint: int | None
    file_tags: dict[Path, AudioTags]


@dataclasses.dataclass
class MusicProposal:
    """Rename proposal for a single audio file."""

    original_path: Path
    new_filename: str | None
    new_path: Path | None
    old_tags: AudioTags
    new_tags: AudioTags | None
    matched_track: TrackInfo | None
    status: str  # "ready" | "skipped" | "no_match" | "conflict" | "error"
    error_message: str | None = None


@dataclasses.dataclass
class AlbumProposal:
    """Rename proposals for all files in an album + optional folder rename."""

    group: AlbumGroup
    release: ReleaseDetails | None
    tracks: list[MusicProposal]
    new_folder_name: str | None
    new_folder_path: Path | None
    folder_status: str  # "ready" | "skipped" | "conflict" | "none"
    folder_error: str | None = None


class MusicRenamer:
    def __init__(self, mb: MusicBrainzClient):
        self.mb = mb

    # ---------- Tag I/O ----------

    def read_tags(self, filepath: Path) -> AudioTags:
        """Read tags from an audio file. Returns empty AudioTags on failure."""
        try:
            audio = mutagen.File(str(filepath), easy=True)
        except Exception:
            return AudioTags()
        if audio is None:
            return AudioTags()

        raw: dict[str, str] = {}
        for key, field in _EASY_KEYS.items():
            val = audio.get(key)
            if val:
                raw[field] = val[0] if isinstance(val, list) else str(val)

        track, total = _parse_track(raw.get("track_raw"))
        return AudioTags(
            title=raw.get("title"),
            artist=raw.get("artist"),
            albumartist=raw.get("albumartist"),
            album=raw.get("album"),
            track=track,
            track_total=total,
            year=_parse_year(raw.get("date_raw")),
            genre=raw.get("genre"),
        )

    def write_tags(self, filepath: Path, tags: AudioTags) -> None:
        """Write tags to an audio file. Raises on failure."""
        audio = mutagen.File(str(filepath), easy=True)
        if audio is None:
            raise ValueError(f"Unsupported audio format: {filepath.name}")
        if audio.tags is None:
            audio.add_tags()

        if tags.title is not None:
            audio["title"] = tags.title
        if tags.artist is not None:
            audio["artist"] = tags.artist
        if tags.albumartist is not None:
            audio["albumartist"] = tags.albumartist
        if tags.album is not None:
            audio["album"] = tags.album
        if tags.track is not None:
            track_str = (
                f"{tags.track}/{tags.track_total}"
                if tags.track_total else str(tags.track)
            )
            audio["tracknumber"] = track_str
        if tags.year is not None:
            audio["date"] = str(tags.year)
        if tags.genre is not None:
            audio["genre"] = tags.genre

        audio.save()

    # ---------- Scanning & grouping ----------

    def scan(self, path: Path, recursive: bool = False) -> list[AlbumGroup]:
        """Collect audio files and group them by containing folder."""
        if path.is_file():
            if path.suffix.lower() not in AUDIO_EXTENSIONS:
                return []
            return [self._build_group(path.parent, [path])]

        if not path.is_dir():
            return []

        files_by_folder: dict[Path, list[Path]] = {}
        iterator = path.rglob("*") if recursive else path.iterdir()
        for f in sorted(iterator):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                files_by_folder.setdefault(f.parent, []).append(f)

        return [
            self._build_group(folder, sorted(files))
            for folder, files in sorted(files_by_folder.items())
        ]

    def _build_group(self, folder: Path, files: list[Path]) -> AlbumGroup:
        """Read tags for each file, derive album/artist/year hints."""
        file_tags: dict[Path, AudioTags] = {f: self.read_tags(f) for f in files}

        albums = {t.album for t in file_tags.values() if t.album}
        album_hint = albums.pop() if len(albums) == 1 else None

        artists = {
            t.albumartist or t.artist
            for t in file_tags.values()
            if t.albumartist or t.artist
        }
        artist_hint = artists.pop() if len(artists) == 1 else None

        years = {t.year for t in file_tags.values() if t.year}
        year_hint = min(years) if years else None

        # Fall back to folder name if tags give nothing.
        if not album_hint:
            album_hint = _album_from_folder(folder.name)

        return AlbumGroup(
            folder=folder,
            files=files,
            album_hint=album_hint,
            artist_hint=artist_hint,
            year_hint=year_hint,
            file_tags=file_tags,
        )

    # ---------- Lookup ----------

    def search_releases(
        self, album: str, artist: str | None = None,
    ) -> list[ReleaseSearchResult]:
        try:
            return self.mb.search_release(album, artist)
        except requests.RequestException:
            return []

    def get_release(self, release_id: str) -> ReleaseDetails | None:
        try:
            return self.mb.get_release(release_id)
        except requests.RequestException:
            return None

    def auto_match(self, group: AlbumGroup) -> ReleaseDetails | None:
        """Best-effort: pick top search result whose track count matches file count."""
        if not group.album_hint:
            return None
        results = self.search_releases(group.album_hint, group.artist_hint)
        if not results:
            return None

        n = len(group.files)
        # Prefer releases whose track count matches file count, else top score.
        matched = [r for r in results if r.track_count == n]
        candidate = matched[0] if matched else results[0]
        return self.get_release(candidate.release_id)

    # ---------- Proposal building ----------

    def build_album_proposal(
        self,
        group: AlbumGroup,
        release: ReleaseDetails | None,
        rename_folder: bool = True,
    ) -> AlbumProposal:
        """Build a per-track proposal + folder rename proposal."""
        track_proposals = [
            self._build_track_proposal(f, group, release)
            for f in group.files
        ]

        folder_name: str | None = None
        folder_path: Path | None = None
        folder_status = "none"
        folder_error: str | None = None

        if rename_folder and release:
            folder_name = format_album_folder(release.title, release.year)
            folder_path = group.folder.parent / folder_name
            if group.folder.name == folder_name:
                folder_status = "skipped"
                folder_error = "Folder already correct"
            elif folder_path.exists() and folder_path != group.folder:
                folder_status = "conflict"
                folder_error = f"Target folder already exists: {folder_name}"
            else:
                folder_status = "ready"

        return AlbumProposal(
            group=group,
            release=release,
            tracks=track_proposals,
            new_folder_name=folder_name,
            new_folder_path=folder_path,
            folder_status=folder_status,
            folder_error=folder_error,
        )

    def _build_track_proposal(
        self,
        filepath: Path,
        group: AlbumGroup,
        release: ReleaseDetails | None,
    ) -> MusicProposal:
        old_tags = group.file_tags.get(filepath) or self.read_tags(filepath)
        ext = filepath.suffix.lstrip(".").lower()

        if release is None:
            # No match — try parsing filename for a track number + title.
            return MusicProposal(
                original_path=filepath,
                new_filename=None,
                new_path=None,
                old_tags=old_tags,
                new_tags=None,
                matched_track=None,
                status="no_match",
                error_message="No release match",
            )

        matched = _match_track(filepath, old_tags, group, release)
        if matched is None:
            return MusicProposal(
                original_path=filepath,
                new_filename=None,
                new_path=None,
                old_tags=old_tags,
                new_tags=None,
                matched_track=None,
                status="no_match",
                error_message="No matching track in release",
            )

        total = len(release.tracks)
        new_filename = format_track_filename(
            matched.position, matched.title, ext, total_tracks=total,
        )
        new_path = filepath.parent / new_filename

        new_tags = AudioTags(
            title=matched.title,
            artist=old_tags.artist or release.artist,
            albumartist=release.artist,
            album=release.title,
            track=matched.position,
            track_total=total,
            year=release.year or old_tags.year,
            genre=old_tags.genre,
        )

        if filepath.name == new_filename:
            return MusicProposal(
                original_path=filepath,
                new_filename=new_filename,
                new_path=new_path,
                old_tags=old_tags,
                new_tags=new_tags,
                matched_track=matched,
                status="skipped",
                error_message="Filename already correct",
            )

        if new_path.exists() and new_path != filepath:
            return MusicProposal(
                original_path=filepath,
                new_filename=new_filename,
                new_path=new_path,
                old_tags=old_tags,
                new_tags=new_tags,
                matched_track=matched,
                status="conflict",
                error_message=f"Target exists: {new_filename}",
            )

        return MusicProposal(
            original_path=filepath,
            new_filename=new_filename,
            new_path=new_path,
            old_tags=old_tags,
            new_tags=new_tags,
            matched_track=matched,
            status="ready",
        )

    # ---------- Execution ----------

    def execute(
        self,
        proposal: AlbumProposal,
        apply_tags: bool = True,
        rename_files: bool = True,
        rename_folder: bool = True,
    ) -> tuple[int, list[str]]:
        """Apply tags, rename files, rename folder.

        Returns (files_processed, errors).
        """
        errors: list[str] = []
        processed = 0

        # 1) Write tags + rename files in-place first (folder path still valid).
        for track in proposal.tracks:
            if track.status != "ready":
                continue
            try:
                if apply_tags and track.new_tags is not None:
                    self.write_tags(track.original_path, track.new_tags)
                if rename_files and track.new_path is not None:
                    track.original_path.rename(track.new_path)
                processed += 1
            except (OSError, ValueError) as e:
                errors.append(f"{track.original_path.name}: {e}")

        # 2) Folder rename last.
        if (
            rename_folder
            and proposal.folder_status == "ready"
            and proposal.new_folder_path is not None
        ):
            try:
                proposal.group.folder.rename(proposal.new_folder_path)
            except OSError as e:
                errors.append(f"Folder rename failed: {e}")

        return processed, errors


# ---------- Helpers ----------


def _parse_track(raw: str | None) -> tuple[int | None, int | None]:
    """Parse '1', '01', '1/12' into (track, total)."""
    if not raw:
        return None, None
    parts = str(raw).split("/")
    try:
        track = int(parts[0])
    except (ValueError, IndexError):
        return None, None
    total = None
    if len(parts) > 1:
        try:
            total = int(parts[1])
        except ValueError:
            pass
    return track, total


def _parse_year(raw: str | None) -> int | None:
    """Parse year from '2010', '2010-01-05', etc."""
    if not raw:
        return None
    m = re.match(r"(\d{4})", str(raw))
    return int(m.group(1)) if m else None


def _album_from_folder(folder_name: str) -> str | None:
    """Heuristic: strip leading '[year]' or 'Artist - ' from folder name."""
    name = folder_name.strip()
    if not name:
        return None
    # Strip leading 'Artist - ' first so 'Artist - [year] Album' collapses.
    if " - " in name:
        name = name.split(" - ", 1)[1].strip()
    # Strip leading year: '[2010] Album' or '(2010) Album'
    m = re.match(r"^[\[\(]\d{4}[\]\)]\s*(.+)$", name)
    if m:
        return m.group(1).strip()
    # Strip trailing year: 'Album (2010)' or 'Album [2010]'
    m = re.match(r"^(.+?)\s*[\[\(]\d{4}[\]\)]\s*$", name)
    if m:
        name = m.group(1).strip()
    return name


def _match_track(
    filepath: Path,
    tags: AudioTags,
    group: AlbumGroup,
    release: ReleaseDetails,
) -> TrackInfo | None:
    """Find the release track corresponding to this file.

    Strategy:
      1) Use track tag number if it exists in the release.
      2) Parse leading digits from filename.
      3) Positional fallback: index in sorted file list.
    """
    by_pos = {t.position: t for t in release.tracks}

    if tags.track is not None and tags.track in by_pos:
        return by_pos[tags.track]

    m = re.match(r"^\s*(\d{1,3})\b", filepath.stem)
    if m:
        n = int(m.group(1))
        if n in by_pos:
            return by_pos[n]

    # Positional fallback — only if file count matches track count exactly.
    if len(group.files) == len(release.tracks):
        try:
            idx = group.files.index(filepath)
        except ValueError:
            return None
        return release.tracks[idx]

    return None
