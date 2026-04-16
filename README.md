# Retitle

Retitle takes messy media filenames and renames them into clean, human-readable names by looking up metadata from online databases.

**Before:**
```
My.Show.S01E01.1080p.x265.mkv
Some.Movie.2024.BluRay.x264.mkv
Another.Show.S03E05-E06.720p.mkv
```

**After:**
```
My Show - S01E01 - Pilot.mkv
Some Movie (2024).mkv
Another Show - S03E05E06 - Episode Title.mkv
```

## How it works

1. Parses the filename to extract the show/movie name, season, episode, year, etc.
2. Looks up the metadata against [TVMaze](https://www.tvmaze.com/api) (for TV shows) and [TMDB](https://www.themoviedb.org/) (for movies).
3. Renames the file using the canonical title and episode name from the API.

If no API returns a match, the file still gets cleaned up using the parsed info — dots and junk stripped, proper formatting applied.

## Setup

Requires Python 3.11+.

```bash
pip install -r requirements.txt
```

Or install as a package (makes the `retitle` command available globally):

```bash
pip install -e .
```

### TMDB API key (optional, needed for movies)

TV show renaming works out of the box via TVMaze (no auth required). For movie support, you need a free TMDB API key:

1. Create an account at [themoviedb.org](https://www.themoviedb.org/)
2. Go to Settings > API and request a key (select "Personal" use)
3. Copy `.env.example` to `.env` and paste your key:

```
TMDB_API_KEY=your_key_here
```

Without a TMDB key, movies are still renamed using parsed filename data — just without canonical title verification.

## Usage

### GUI

The quickest way to get going. Browse to a file or folder, preview the renames, click a button.

```bash
retitle gui
```

Or just double-click `run.bat` — it installs dependencies and launches the GUI.

The GUI shows a table with three columns: status, original filename, and proposed new name. Green rows are ready to rename, yellow means a conflict or no match. You can select specific rows to rename individually, or hit "Rename All" to process everything at once.

### CLI

For scripting or quick one-offs.

**Single file:**
```bash
retitle rename "My.Show.S01E01.1080p.x265.mkv"
```

**Entire directory:**
```bash
retitle rename /path/to/downloads/
```

**Preview without renaming (dry run):**
```bash
retitle rename /path/to/downloads/ --dry-run
```

**Skip the confirmation prompt:**
```bash
retitle rename /path/to/downloads/ --yes
```

**Scan subdirectories:**
```bash
retitle rename /path/to/downloads/ --recursive
```

Flags can be combined: `retitle rename /path/ -n -r` previews all files recursively.

### Output format

| Type | Format |
|------|--------|
| TV episodes | `Show Name - S01E01 - Episode Title.ext` |
| Multi-episode | `Show Name - S01E01E02 - Episode Title.ext` |
| Movies | `Movie Name (2024).ext` |

### Supported file types

`.mkv` `.mp4` `.avi` `.mov` `.wmv` `.flv` `.ts` `.webm` `.m4v`

## API details

**TVMaze** — Used for TV show lookups. No authentication required. Rate limit is 20 requests per 10 seconds, but in practice a full season scan only makes 2 API calls (one search + one episode list fetch) thanks to in-memory caching.

**TMDB** — Used for movie lookups and as a fallback for TV shows that TVMaze doesn't have. Requires a free API key. Rate limit is ~40 requests per second.

The lookup chain for TV shows is: TVMaze -> TMDB -> GuessIt (parsed filename). For movies: TMDB -> GuessIt.
