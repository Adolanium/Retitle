import dataclasses
import os
from guessit import guessit


@dataclasses.dataclass
class ParsedMedia:
    title: str | None
    media_type: str  # "episode" or "movie"
    season: int | None
    episode: int | list[int] | None
    year: int | None
    extension: str
    original_filename: str
    confidence: str  # "high", "medium", "low"


def parse_filename(filename: str) -> ParsedMedia:
    """Parse a media filename and return structured metadata."""
    basename = os.path.basename(filename)
    guess = guessit(basename)

    title = guess.get("title")
    media_type = guess.get("type", "movie")  # guessit returns "episode" or "movie"
    season = guess.get("season")
    episode = guess.get("episode")
    year = guess.get("year")
    extension = guess.get("container", os.path.splitext(basename)[1].lstrip("."))

    # Edge case: hyphenated titles (e.g., Spider-Man)
    # GuessIt sometimes puts text before the hyphen into release_group
    release_group = guess.get("release_group")
    if release_group and title:
        # Check if the original filename starts with the release_group
        clean_name = basename.replace(".", " ").replace("_", " ")
        if clean_name.lower().startswith(release_group.lower()):
            # The release_group is actually part of the title
            title = f"{release_group}-{title}"

    # Edge case: "Part" titles (e.g., Dune Part Two)
    part = guess.get("part")
    if part and title and media_type == "movie":
        title = f"{title} Part {part}"

    # Determine confidence
    confidence = _assess_confidence(title, media_type, season, episode, year)

    return ParsedMedia(
        title=title,
        media_type=media_type,
        season=season,
        episode=episode,
        year=year,
        extension=extension,
        original_filename=basename,
        confidence=confidence,
    )


def _assess_confidence(
    title: str | None,
    media_type: str,
    season: int | None,
    episode: int | list[int] | None,
    year: int | None,
) -> str:
    if not title:
        return "low"
    if media_type == "episode" and season is not None and episode is not None:
        return "high"
    if media_type == "movie" and year is not None:
        return "high"
    if media_type == "episode" and (season is not None or episode is not None):
        return "medium"
    return "medium"
