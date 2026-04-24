from pathlib import Path

import click

from retitle.api.musicbrainz import MusicBrainzClient
from retitle.api.opensubtitles import OpenSubtitlesClient
from retitle.api.tmdb import TMDBClient
from retitle.api.tvmaze import TVMazeClient
from retitle.music import AUDIO_EXTENSIONS, AlbumProposal, MusicRenamer
from retitle.renamer import MEDIA_EXTENSIONS, RenameProposal, Renamer
from retitle.subtitles import SubtitleDownloader, SubtitleProposal


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Retitle - Rename media files using TV show/movie metadata."""


@cli.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--dry-run", "-n", is_flag=True, help="Preview renames without executing")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.option("--recursive", "-r", is_flag=True, help="Process subdirectories")
def rename(path: str, dry_run: bool, yes: bool, recursive: bool):
    """Rename media files with proper show/movie titles.

    PATH can be a single file or a directory.
    """
    target = Path(path).resolve()
    tvmaze = TVMazeClient()
    tmdb = None
    try:
        tmdb = TMDBClient()
    except ValueError:
        click.secho("TMDB API key not set — movie lookups disabled. Set TMDB_API_KEY in .env", fg="yellow")
    renamer = Renamer(tvmaze, tmdb)

    if target.is_file():
        if target.suffix.lower() not in MEDIA_EXTENSIONS:
            click.echo(f"Not a recognized media file: {target.name}")
            click.echo(f"Supported: {', '.join(sorted(MEDIA_EXTENSIONS))}")
            return
        proposals = [renamer.propose_rename(target)]
    elif target.is_dir():
        click.echo(f"Scanning {'recursively ' if recursive else ''}{target} ...")
        proposals = renamer.propose_batch(target, recursive=recursive)
        if not proposals:
            click.echo("No media files found.")
            return
    else:
        click.echo(f"Not a valid file or directory: {path}")
        return

    _display_proposals(proposals)

    ready = [p for p in proposals if p.status == "ready"]
    if not ready:
        click.echo("\nNo files to rename.")
        return

    if dry_run:
        click.echo(f"\nDry run: {len(ready)} file(s) would be renamed.")
        return

    if not yes:
        click.echo()
        if not click.confirm(f"Rename {len(ready)} file(s)?", default=False):
            click.echo("Cancelled.")
            return

    # Execute renames
    success = 0
    for proposal in ready:
        try:
            if renamer.execute_rename(proposal):
                success += 1
        except OSError as e:
            click.echo(f"  !! Error renaming {proposal.original_path.name}: {e}")

    click.echo(f"\nRenamed {success}/{len(ready)} file(s).")


def _display_proposals(proposals: list[RenameProposal]):
    """Display rename proposals to the user."""
    total = len(proposals)
    for i, p in enumerate(proposals, 1):
        prefix = f"[{i}/{total}]"

        if p.status == "ready":
            click.echo(f"\n{prefix} {p.original_path.name}")
            click.echo(f"   -> {p.new_filename}")

        elif p.status == "conflict":
            click.echo(f"\n{prefix} {p.original_path.name}")
            click.echo(f"   -> {p.new_filename}")
            click.secho(f"   !! Conflict: target already exists. Skipping.", fg="yellow")

        elif p.status == "skipped":
            click.echo(f"\n{prefix} {p.original_path.name}")
            click.secho(f"   -- Already correct. Skipping.", fg="cyan")

        elif p.status == "no_match":
            click.echo(f"\n{prefix} {p.original_path.name}")
            click.secho(f"   !! {p.error_message}. Skipping.", fg="yellow")

        elif p.status == "error":
            click.echo(f"\n{prefix} {p.original_path.name}")
            click.secho(f"   !! {p.error_message}. Skipping.", fg="red")


@cli.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--language", "-l", default="en", help="Subtitle language code (e.g., en, fr, es)")
@click.option("--dry-run", "-n", is_flag=True, help="Search only, don't download")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.option("--recursive", "-r", is_flag=True, help="Process subdirectories")
def subtitles(path: str, language: str, dry_run: bool, yes: bool, recursive: bool):
    """Download subtitles for media files.

    PATH can be a single file or a directory.
    Searches OpenSubtitles for matching subtitles and saves .srt files
    alongside the media files.
    """
    target = Path(path).resolve()

    try:
        os_client = OpenSubtitlesClient()
    except ValueError:
        click.secho(
            "OpenSubtitles not configured. Set OPENSUBTITLES_API_KEY, "
            "OPENSUBTITLES_USERNAME, and OPENSUBTITLES_PASSWORD in .env",
            fg="red",
        )
        return

    downloader = SubtitleDownloader(os_client, language=language)

    if target.is_file():
        if target.suffix.lower() not in MEDIA_EXTENSIONS:
            click.echo(f"Not a recognized media file: {target.name}")
            click.echo(f"Supported: {', '.join(sorted(MEDIA_EXTENSIONS))}")
            return
        from retitle.parser import parse_filename

        parsed = parse_filename(target.name)
        proposals = [downloader.propose_subtitle(target, parsed)]
    elif target.is_dir():
        click.echo(f"Scanning {'recursively ' if recursive else ''}{target} ...")
        proposals = downloader.propose_batch(target, recursive=recursive)
        if not proposals:
            click.echo("No media files found.")
            return
    else:
        click.echo(f"Not a valid file or directory: {path}")
        return

    _display_subtitle_proposals(proposals)

    found = [p for p in proposals if p.status == "found"]
    if not found:
        click.echo("\nNo subtitles available to download.")
        return

    if dry_run:
        click.echo(f"\nDry run: {len(found)} subtitle(s) found.")
        return

    if not yes:
        click.echo()
        if not click.confirm(f"Download {len(found)} subtitle(s)?", default=False):
            click.echo("Cancelled.")
            return

    success = 0
    for proposal in found:
        try:
            if downloader.execute_download(proposal):
                success += 1
        except Exception as e:
            click.echo(f"  !! Error downloading for {proposal.media_path.name}: {e}")

    click.echo(f"\nDownloaded {success}/{len(found)} subtitle(s).")


def _display_subtitle_proposals(proposals: list[SubtitleProposal]):
    """Display subtitle search results to the user."""
    total = len(proposals)
    for i, p in enumerate(proposals, 1):
        prefix = f"[{i}/{total}]"

        if p.status == "found" and p.selected_result:
            click.echo(f"\n{prefix} {p.media_path.name}")
            release = p.selected_result.release or "Unknown release"
            count = f"{p.selected_result.download_count:,}"
            click.secho(
                f"   Sub: Found ({p.language}) — \"{release}\" ({count} downloads)",
                fg="green",
            )

        elif p.status == "not_found":
            click.echo(f"\n{prefix} {p.media_path.name}")
            click.secho(f"   Sub: Not found ({p.language})", fg="yellow")

        elif p.status == "skipped":
            click.echo(f"\n{prefix} {p.media_path.name}")
            click.secho(f"   Sub: Already exists. Skipping.", fg="cyan")

        elif p.status == "error":
            click.echo(f"\n{prefix} {p.media_path.name}")
            click.secho(f"   Sub: Error — {p.error_message}", fg="red")


@cli.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--dry-run", "-n", is_flag=True, help="Preview without renaming or tagging")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.option("--recursive", "-r", is_flag=True, help="Process subdirectories")
@click.option(
    "--no-folder-rename",
    is_flag=True,
    help="Don't rename the album folder, only track files",
)
@click.option(
    "--no-tag-write",
    is_flag=True,
    help="Don't write ID3/metadata tags, only rename files",
)
def music(
    path: str,
    dry_run: bool,
    yes: bool,
    recursive: bool,
    no_folder_rename: bool,
    no_tag_write: bool,
):
    """Rename music files + apply tags using MusicBrainz metadata.

    PATH can be a single audio file or a directory containing album folder(s).
    Each folder is treated as one album. MusicBrainz is used to look up the
    album and its track list, then files are tagged and renamed as
    'NN Track Title.ext' and the parent folder as '[Year] Album Name'.
    """
    target = Path(path).resolve()
    mb = MusicBrainzClient()
    renamer = MusicRenamer(mb)

    click.echo(f"Scanning {'recursively ' if recursive else ''}{target} ...")
    groups = renamer.scan(target, recursive=recursive)
    if not groups:
        click.echo("No audio files found.")
        return

    album_proposals: list[AlbumProposal] = []
    for group in groups:
        click.echo(f"\n[{group.folder.name}] matching album ...")
        release = renamer.auto_match(group)
        proposal = renamer.build_album_proposal(
            group,
            release,
            rename_folder=not no_folder_rename,
        )
        album_proposals.append(proposal)
        _display_album_proposal(proposal)

    total_ready = sum(
        1 for ap in album_proposals for t in ap.tracks if t.status == "ready"
    )
    folder_ready = sum(1 for ap in album_proposals if ap.folder_status == "ready")

    if total_ready == 0 and folder_ready == 0:
        click.echo("\nNothing to change.")
        return

    if dry_run:
        click.echo(
            f"\nDry run: would process {total_ready} track(s) "
            f"across {folder_ready} folder rename(s).",
        )
        return

    if not yes:
        click.echo()
        if not click.confirm(
            f"Apply changes to {total_ready} track(s) and rename "
            f"{folder_ready} folder(s)?",
            default=False,
        ):
            click.echo("Cancelled.")
            return

    total_processed = 0
    for ap in album_proposals:
        processed, errors = renamer.execute(
            ap,
            apply_tags=not no_tag_write,
            rename_files=True,
            rename_folder=not no_folder_rename,
        )
        total_processed += processed
        for err in errors:
            click.secho(f"  !! {err}", fg="red")

    click.echo(f"\nProcessed {total_processed}/{total_ready} track(s).")


def _display_album_proposal(proposal: AlbumProposal):
    group = proposal.group
    if proposal.release is None:
        click.secho(
            f"  !! No MusicBrainz match (hint: album='{group.album_hint}' "
            f"artist='{group.artist_hint}')",
            fg="yellow",
        )
        return

    r = proposal.release
    year = f" [{r.year}]" if r.year else ""
    click.secho(f"  Matched: {r.artist} - {r.title}{year}", fg="green")

    if proposal.folder_status == "ready":
        click.echo(f"   Folder -> {proposal.new_folder_name}")
    elif proposal.folder_status == "conflict":
        click.secho(f"   Folder !! {proposal.folder_error}", fg="yellow")

    for t in proposal.tracks:
        if t.status == "ready":
            click.echo(f"    {t.original_path.name}")
            click.echo(f"      -> {t.new_filename}")
        elif t.status == "conflict":
            click.secho(
                f"    !! {t.original_path.name}: {t.error_message}", fg="yellow",
            )
        elif t.status == "no_match":
            click.secho(
                f"    !! {t.original_path.name}: {t.error_message}", fg="yellow",
            )
        elif t.status == "skipped":
            click.secho(f"    -- {t.original_path.name}: already correct", fg="cyan")


@cli.command()
def gui():
    """Launch the graphical interface."""
    from retitle.gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    cli()
