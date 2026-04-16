import dataclasses
from pathlib import Path

import requests

from retitle.api.opensubtitles import (
    OpenSubtitlesClient,
    SubtitleSearchResult,
)
from retitle.parser import ParsedMedia, parse_filename
from retitle.renamer import MEDIA_EXTENSIONS


@dataclasses.dataclass
class SubtitleProposal:
    media_path: Path
    subtitle_path: Path | None
    search_results: list[SubtitleSearchResult]
    selected_result: SubtitleSearchResult | None
    status: str  # "found", "not_found", "downloaded", "error", "skipped"
    error_message: str | None = None
    language: str = "en"


class SubtitleDownloader:
    def __init__(self, client: OpenSubtitlesClient, language: str = "en"):
        self.client = client
        self.language = language

    def propose_subtitle(
        self, media_path: Path, parsed: ParsedMedia
    ) -> SubtitleProposal:
        """Search for subtitles for a single media file."""
        subtitle_path = self._build_subtitle_path(media_path)

        # Skip if subtitle already exists
        if subtitle_path.exists():
            return SubtitleProposal(
                media_path=media_path,
                subtitle_path=subtitle_path,
                search_results=[],
                selected_result=None,
                status="skipped",
                error_message="Subtitle already exists",
                language=self.language,
            )

        try:
            results = self._search_for_media(parsed)
        except requests.RequestException as e:
            return SubtitleProposal(
                media_path=media_path,
                subtitle_path=subtitle_path,
                search_results=[],
                selected_result=None,
                status="error",
                error_message=str(e),
                language=self.language,
            )

        if not results:
            return SubtitleProposal(
                media_path=media_path,
                subtitle_path=subtitle_path,
                search_results=[],
                selected_result=None,
                status="not_found",
                language=self.language,
            )

        return SubtitleProposal(
            media_path=media_path,
            subtitle_path=subtitle_path,
            search_results=results,
            selected_result=results[0],  # Best match (highest download count)
            status="found",
            language=self.language,
        )

    def propose_batch(
        self, directory: Path, recursive: bool = False
    ) -> list[SubtitleProposal]:
        """Search subtitles for all media files in a directory."""
        proposals = []
        if recursive:
            files = sorted(directory.rglob("*"))
        else:
            files = sorted(directory.iterdir())

        for f in files:
            if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS:
                parsed = parse_filename(f.name)
                proposals.append(self.propose_subtitle(f, parsed))

        return proposals

    def execute_download(self, proposal: SubtitleProposal) -> bool:
        """Download the subtitle and save it alongside the media file."""
        if proposal.status != "found" or not proposal.selected_result:
            return False
        if proposal.subtitle_path is None:
            return False

        dl_result = self.client.download(proposal.selected_result.file_id)
        content = self.client.download_content(dl_result.download_url)
        proposal.subtitle_path.write_bytes(content)
        proposal.status = "downloaded"
        return True

    def _search_for_media(
        self, parsed: ParsedMedia
    ) -> list[SubtitleSearchResult]:
        """Search OpenSubtitles using parsed metadata."""
        if not parsed.title:
            return []

        ep_num = None
        if parsed.episode is not None:
            ep_num = (
                parsed.episode[0]
                if isinstance(parsed.episode, list)
                else parsed.episode
            )

        return self.client.search(
            query=parsed.title,
            season_number=parsed.season,
            episode_number=ep_num,
            year=parsed.year,
            languages=self.language,
            media_type="episode" if parsed.media_type == "episode" else "movie",
        )

    def _build_subtitle_path(self, media_path: Path) -> Path:
        """Generate .srt path: <stem>.<lang>.srt alongside media file."""
        return media_path.with_suffix(f".{self.language}.srt")
