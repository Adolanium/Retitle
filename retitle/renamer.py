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


@dataclasses.dataclass
class SearchMatch:
    """A search result from TVMaze or TMDB for user selection."""

    source: str
    title: str
    year: int | None = None
    extra_info: str = ""
    tvmaze_show_id: int | None = None
    tmdb_id: int | None = None


class Renamer:
    def __init__(self, tvmaze: TVMazeClient, tmdb: TMDBClient | None = None):
        self.tvmaze = tvmaze
        self.tmdb = tmdb

    def propose_rename(self, filepath: Path, parsed_only: bool = False) -> RenameProposal:
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

        if parsed_only:
            return self._propose_parsed_only(filepath, parsed)

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

    def _propose_parsed_only(
        self, filepath: Path, parsed: ParsedMedia,
    ) -> RenameProposal:
        """Generate proposal using only parsed data, no API lookup."""
        if (
            parsed.media_type == "episode"
            and parsed.season is not None
            and parsed.episode is not None
            and parsed.title
        ):
            new_filename = format_tv_filename(
                show_name=parsed.title,
                season=parsed.season,
                episode=parsed.episode,
                episode_title=None,
                ext=parsed.extension,
            )
        elif parsed.title and parsed.year:
            new_filename = format_movie_filename(
                parsed.title, parsed.year, parsed.extension,
            )
        elif parsed.title:
            new_filename = f"{parsed.title}.{parsed.extension}"
        else:
            return RenameProposal(
                original_path=filepath,
                new_filename=None,
                new_path=None,
                parsed=parsed,
                api_result=None,
                status="no_match",
                error_message="Could not identify media from filename",
            )
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

    # --- Interactive match selection ---

    def search_tv_matches(self, title: str) -> list[SearchMatch]:
        """Search TVMaze and TMDB for TV shows matching the title."""
        matches: list[SearchMatch] = []
        try:
            tvmaze_results = self.tvmaze.search_show(title)
            for r in tvmaze_results[:5]:
                matches.append(SearchMatch(
                    source="TVMaze",
                    title=r.show_name,
                    extra_info=f"Score: {r.score:.2f}",
                    tvmaze_show_id=r.show_id,
                ))
        except Exception:
            pass

        if self.tmdb:
            try:
                tmdb_results = self.tmdb.search_tv(title)
                for r in tmdb_results[:5]:
                    first_air = r.get("first_air_date", "")
                    yr = int(first_air[:4]) if first_air and len(first_air) >= 4 else None
                    matches.append(SearchMatch(
                        source="TMDB",
                        title=r["name"],
                        year=yr,
                        extra_info=f"First aired: {first_air}" if first_air else "",
                        tmdb_id=r["id"],
                    ))
            except Exception:
                pass

        return matches

    def search_movie_matches(
        self, title: str, year: int | None = None,
    ) -> list[SearchMatch]:
        """Search TMDB for movies matching the title."""
        matches: list[SearchMatch] = []
        if self.tmdb:
            try:
                results = self.tmdb.search_movie(title)
                for r in results[:10]:
                    release_date = r.get("release_date", "")
                    movie_year = (
                        int(release_date[:4])
                        if release_date and len(release_date) >= 4
                        else None
                    )
                    matches.append(SearchMatch(
                        source="TMDB",
                        title=r["title"],
                        year=movie_year,
                        tmdb_id=r["id"],
                    ))
            except Exception:
                pass
        return matches

    def propose_tv_with_match(
        self,
        filepath: Path,
        parsed: ParsedMedia,
        match: SearchMatch,
        season: int,
        episode: int | list[int],
    ) -> RenameProposal:
        """Generate a TV rename proposal using a user-selected show match."""
        ep_num = episode[0] if isinstance(episode, list) else episode
        result = None

        if match.tvmaze_show_id is not None:
            try:
                result = self.tvmaze._find_episode(
                    match.tvmaze_show_id, match.title, season, ep_num,
                )
            except Exception:
                pass
        elif match.tmdb_id is not None and self.tmdb:
            try:
                episodes = self.tmdb.get_season_episodes(match.tmdb_id, season)
                for ep in episodes:
                    if ep.get("episode_number") == ep_num:
                        result = EpisodeLookupResult(
                            show_name=match.title,
                            episode_title=ep.get("name", ""),
                            season=season,
                            episode=ep_num,
                            air_date=ep.get("air_date"),
                        )
                        break
            except Exception:
                pass

        if result:
            new_filename = format_tv_filename(
                show_name=result.show_name,
                season=result.season,
                episode=episode,
                episode_title=result.episode_title,
                ext=parsed.extension,
            )
        else:
            new_filename = format_tv_filename(
                show_name=match.title,
                season=season,
                episode=episode,
                episode_title=None,
                ext=parsed.extension,
            )

        return self._build_proposal(filepath, new_filename, parsed, result)

    def propose_movie_with_match(
        self, filepath: Path, parsed: ParsedMedia, match: SearchMatch,
    ) -> RenameProposal:
        """Generate a movie rename proposal using a user-selected match."""
        year = match.year or parsed.year or 0
        new_filename = format_movie_filename(match.title, year, parsed.extension)
        return self._build_proposal(filepath, new_filename, parsed, None)

    def propose_with_overrides(
        self,
        filepath: Path,
        parsed: ParsedMedia,
        title: str,
        media_type: str,
        season: int | None,
        episode: int | list[int] | None,
        year: int | None,
    ) -> RenameProposal:
        """Generate a rename proposal from manually specified values."""
        if media_type == "episode" and season is not None and episode is not None:
            new_filename = format_tv_filename(
                show_name=title,
                season=season,
                episode=episode,
                episode_title=None,
                ext=parsed.extension,
            )
        elif year:
            new_filename = format_movie_filename(title, year, parsed.extension)
        else:
            new_filename = f"{title}.{parsed.extension}"
        return self._build_proposal(filepath, new_filename, parsed, None)

    def execute_rename(self, proposal: RenameProposal) -> bool:
        """Actually rename the file. Returns True on success."""
        if proposal.status != "ready" or proposal.new_path is None:
            return False
        proposal.original_path.rename(proposal.new_path)
        return True

    def propose_batch(
        self, directory: Path, recursive: bool = False,
        parsed_only: bool = False,
    ) -> list[RenameProposal]:
        """Generate proposals for all media files in a directory."""
        proposals = []
        if recursive:
            files = sorted(directory.rglob("*"))
        else:
            files = sorted(directory.iterdir())

        for f in files:
            if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS:
                proposals.append(self.propose_rename(f, parsed_only=parsed_only))

        return proposals
