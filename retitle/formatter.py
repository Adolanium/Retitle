import re


def format_tv_filename(
    show_name: str,
    season: int,
    episode: int | list[int],
    episode_title: str | None,
    ext: str,
) -> str:
    """Format a TV episode filename.

    Returns: 'Show Name - S01E01 - Episode Title.ext'
    Multi-ep: 'Show Name - S01E01-E02 - Episode Title.ext'
    No title: 'Show Name - S01E01.ext'
    """
    if isinstance(episode, list):
        ep_str = f"S{season:02d}" + "".join(f"E{e:02d}" for e in sorted(episode))
    else:
        ep_str = f"S{season:02d}E{episode:02d}"

    if episode_title:
        name = f"{show_name} - {ep_str} - {episode_title}.{ext}"
    else:
        name = f"{show_name} - {ep_str}.{ext}"

    return sanitize_filename(name)


def format_movie_filename(title: str, year: int, ext: str) -> str:
    """Format a movie filename.

    Returns: 'Movie Name (2024).ext'
    """
    name = f"{title} ({year}).{ext}"
    return sanitize_filename(name)


def format_track_filename(
    track_number: int,
    title: str,
    ext: str,
    total_tracks: int | None = None,
) -> str:
    """Format an audio track filename.

    Returns: '01 Song Title.ext' (or '001 Song Title.ext' if >99 tracks).
    """
    width = 3 if (total_tracks is not None and total_tracks >= 100) else 2
    name = f"{track_number:0{width}d} {title}.{ext}"
    return sanitize_filename(name)


def format_album_folder(album: str, year: int | None) -> str:
    """Format an album folder name.

    With year: '[2010] Album Name'
    No year:   'Album Name'
    """
    year_part = f"[{year}] " if year else ""
    name = f"{year_part}{album}"
    return sanitize_filename(name)


def sanitize_filename(name: str) -> str:
    """Remove/replace characters that are invalid on Windows.

    Invalid chars: < > : " / \\ | ? *
    Also strips trailing dots and spaces.
    """
    # Replace colon with dash (common in episode titles like "Chapter One: The Heir")
    name = name.replace(": ", " - ").replace(":", "-")
    # Remove other illegal characters
    name = re.sub(r'[<>"/\\|?*]', "", name)
    # Strip trailing dots and spaces (Windows restriction)
    name = name.rstrip(". ")
    # Collapse multiple spaces
    name = re.sub(r" {2,}", " ", name)
    return name
