import dataclasses
import os
import time

import requests
from dotenv import load_dotenv


@dataclasses.dataclass
class MovieLookupResult:
    title: str
    year: int
    tmdb_id: int


@dataclasses.dataclass
class TVLookupResult:
    show_name: str
    episode_title: str
    season: int
    episode: int
    air_date: str | None


class TMDBClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str | None = None):
        load_dotenv()
        self.api_key = api_key or os.getenv("TMDB_API_KEY")
        if not self.api_key or self.api_key == "your_api_key_here":
            raise ValueError(
                "TMDB API key not configured. Set TMDB_API_KEY in .env file "
                "or pass it directly."
            )
        self._session = requests.Session()
        self._last_request_time = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < 0.25:
            time.sleep(0.25 - elapsed)
        self._last_request_time = time.time()

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        self._throttle()
        params = params or {}
        params["api_key"] = self.api_key
        url = f"{self.BASE_URL}{endpoint}"
        resp = self._session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def search_movie(
        self, query: str, year: int | None = None
    ) -> list[dict]:
        """Search for movies by title."""
        params = {"query": query}
        if year:
            params["year"] = str(year)
        data = self._get("/search/movie", params)
        return data.get("results", [])

    def search_tv(self, query: str) -> list[dict]:
        """Search for TV shows (fallback when TVMaze fails)."""
        data = self._get("/search/tv", params={"query": query})
        return data.get("results", [])

    def get_season_episodes(self, tv_id: int, season: int) -> list[dict]:
        """Get all episodes for a season of a TV show."""
        data = self._get(f"/tv/{tv_id}/season/{season}")
        return data.get("episodes", [])

    def get_movie_details(
        self, query: str, year: int | None = None
    ) -> MovieLookupResult | None:
        """High-level: search and return best movie match."""
        results = self.search_movie(query, year)
        if not results:
            # Retry without year if no results
            if year:
                results = self.search_movie(query)
            if not results:
                return None

        best = results[0]
        release_date = best.get("release_date", "")
        movie_year = int(release_date[:4]) if release_date and len(release_date) >= 4 else (year or 0)

        return MovieLookupResult(
            title=best["title"],
            year=movie_year,
            tmdb_id=best["id"],
        )

    def get_top_movie_matches(
        self, query: str, year: int | None = None, count: int = 3
    ) -> list[MovieLookupResult]:
        """Return top N movie matches for interactive selection."""
        results = self.search_movie(query, year)
        matches = []
        for r in results[:count]:
            release_date = r.get("release_date", "")
            movie_year = int(release_date[:4]) if release_date and len(release_date) >= 4 else 0
            matches.append(
                MovieLookupResult(
                    title=r["title"],
                    year=movie_year,
                    tmdb_id=r["id"],
                )
            )
        return matches

    def get_episode_title(
        self, show_name: str, season: int, episode: int
    ) -> TVLookupResult | None:
        """Fallback TV lookup via TMDB when TVMaze has no result."""
        results = self.search_tv(show_name)
        if not results:
            return None

        tv_id = results[0]["id"]
        canonical_name = results[0]["name"]

        try:
            episodes = self.get_season_episodes(tv_id, season)
        except requests.HTTPError:
            return None

        for ep in episodes:
            if ep.get("episode_number") == episode:
                return TVLookupResult(
                    show_name=canonical_name,
                    episode_title=ep.get("name", ""),
                    season=season,
                    episode=episode,
                    air_date=ep.get("air_date"),
                )
        return None
