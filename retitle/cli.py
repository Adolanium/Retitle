from pathlib import Path

import click

from retitle.api.tmdb import TMDBClient
from retitle.api.tvmaze import TVMazeClient
from retitle.renamer import MEDIA_EXTENSIONS, RenameProposal, Renamer


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
def gui():
    """Launch the graphical interface."""
    from retitle.gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    cli()
