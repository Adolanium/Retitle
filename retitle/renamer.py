import dataclasses
from pathlib import Path

import requests

from retitle.api.tmdb import TMDBClient
from retitle.api.tvmaze import EpisodeLookupResult, TVMazeClient
from retitle.formatter import format_movie_filename, format_tv_filename
from retitle.parser import ParsedMedia, parse_filename

MEDIA_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".ts", ".webm", ".m4v"}


@dataclasses.dataclass
class RenameProposal:
    original_path: Path
    new_filename: str | None
    new_path: Path | None
    parsed: ParsedMedia
    api_result: EpisodeLookupResult | None
    status: str  # "ready", "no_match", "conflict", "skipped", "error"
    error_message: str | None = None


class Renamer:
    def __init__(self, tvmaze: TVMazeClient, tmdb: TMDBClient | None = None):
        self.tvmaze = tvmaze
        self.tmdb = tmdb

    def propose_rename(self, filepath: Path) -> RenameProposal:
        """Parse file, look up metadata, generate proposed new name."""
        parsed = parse_filename(filepath.name)

        if parsed.confidence == "low":
            return RenameProposal(
                original_path=filepath,
                new_filename=None,
                new_path=None,
                parsed=parsed,
                api_result=None,
                status="no_match",
                error_message="Could not identify media from filename",
            )

        if parsed.media_type == "episode":
            return self._propose_tv(filepath, parsed)
        else:
            return self._propose_movie(filepath, parsed)

    def _propose_tv(self, filepath: Path, parsed: ParsedMedia) -> RenameProposal:
        """Handle TV episode rename proposal."""
        if parsed.season is None or parsed.episode is None or parsed.title is None:
            return RenameProposal(
                original_path=filepath,
                new_filename=None,
                new_path=None,
                parsed=parsed,
                api_result=None,
                status="no_match",
                error_message="Missing season/episode/title info",
            )

        # For multi-episode files, look up the first episode
        ep_num = parsed.episode[0] if isinstance(parsed.episode, list) else parsed.episode

        try:
            result = self.tvmaze.get_episode_title(
                parsed.title, parsed.season, ep_num
            )
        except requests.RequestException as e:
            result = None

        # Fallback to TMDB for TV if TVMaze found nothing
        if not result and self.tmdb:
            try:
                tmdb_result = self.tmdb.get_episode_title(
                    parsed.title, parsed.season, ep_num
                )
                if tmdb_result:
                    result = EpisodeLookupResult(
                        show_name=tmdb_result.show_name,
                        episode_title=tmdb_result.episode_title,
                        season=tmdb_result.season,
                        episode=tmdb_result.episode,
                        air_date=tmdb_result.air_date,
                    )
            except requests.RequestException:
                pass

        if not result:
            # No API match — still rename with parsed title, just no episode title
            new_filename = format_tv_filename(
                show_name=parsed.title,
                season=parsed.season,
                episode=parsed.episode,
                episode_title=None,
                ext=parsed.extension,
            )
            return self._build_proposal(filepath, new_filename, parsed, None)

        new_filename = format_tv_filename(
            show_name=result.show_name,
            season=result.season,
            episode=parsed.episode,
            episode_title=result.episode_title,
            ext=parsed.extension,
        )
        return self._build_proposal(filepath, new_filename, parsed, result)

    def _propose_movie(self, filepath: Path, parsed: ParsedMedia) -> RenameProposal:
        """Handle movie rename proposal with TMDB lookup."""
        if not parsed.title:
            return RenameProposal(
                original_path=filepath,
                new_filename=None,
                new_path=None,
                parsed=parsed,
                api_result=None,
                status="no_match",
                error_message="Could not identify movie title",
            )

        # Try TMDB lookup if available
        if self.tmdb:
            try:
                movie = self.tmdb.get_movie_details(parsed.title, parsed.year)
                if movie:
                    new_filename = format_movie_filename(
                        movie.title, movie.year, parsed.extension
                    )
                    return self._build_proposal(filepath, new_filename, parsed, None)
            except requests.RequestException:
                pass

        # Fallback: use parsed info directly
        if parsed.year:
            new_filename = format_movie_filename(
                parsed.title, parsed.year, parsed.extension
            )
        else:
            new_filename = f"{parsed.title}.{parsed.extension}"

        return self._build_proposal(filepath, new_filename, parsed, None)

    def _build_proposal(
        self,
        filepath: Path,
        new_filename: str,
        parsed: ParsedMedia,
        api_result: EpisodeLookupResult | None,
    ) -> RenameProposal:
        new_path = filepath.parent / new_filename

        # Skip if name unchanged
        if filepath.name == new_filename:
            return RenameProposal(
                original_path=filepath,
                new_filename=new_filename,
                new_path=new_path,
                parsed=parsed,
                api_result=api_result,
                status="skipped",
                error_message="Filename already correct",
            )

        # Check for conflicts
        if new_path.exists():
            return RenameProposal(
                original_path=filepath,
                new_filename=new_filename,
                new_path=new_path,
                parsed=parsed,
                api_result=api_result,
                status="conflict",
                error_message=f"Target file already exists: {new_filename}",
            )

        return RenameProposal(
            original_path=filepath,
            new_filename=new_filename,
            new_path=new_path,
            parsed=parsed,
            api_result=api_result,
            status="ready",
        )

    def execute_rename(self, proposal: RenameProposal) -> bool:
        """Actually rename the file. Returns True on success."""
        if proposal.status != "ready" or proposal.new_path is None:
            return False
        proposal.original_path.rename(proposal.new_path)
        return True

    def propose_batch(
        self, directory: Path, recursive: bool = False
    ) -> list[RenameProposal]:
        """Generate proposals for all media files in a directory."""
        proposals = []
        if recursive:
            files = sorted(directory.rglob("*"))
        else:
            files = sorted(directory.iterdir())

        for f in files:
            if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS:
                proposals.append(self.propose_rename(f))

        return proposals
