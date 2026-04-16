import dataclasses
import time

import requests


@dataclasses.dataclass
class EpisodeLookupResult:
    show_name: str
    episode_title: str
    season: int
    episode: int
    air_date: str | None


@dataclasses.dataclass
class ShowSearchResult:
    show_id: int
    show_name: str
    score: float


class TVMazeClient:
    BASE_URL = "https://api.tvmaze.com"

    def __init__(self):
        self._session = requests.Session()
        self._show_cache: dict[str, list[ShowSearchResult]] = {}
        self._episode_cache: dict[int, list[dict]] = {}
        self._last_request_time = 0.0

    def _throttle(self):
        """Simple rate limiting: at least 0.5s between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < 0.5:
            time.sleep(0.5 - elapsed)
        self._last_request_time = time.time()

    def _get(self, endpoint: str, params: dict | None = None) -> requests.Response:
        self._throttle()
        url = f"{self.BASE_URL}{endpoint}"
        resp = self._session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp

    def search_show(self, query: str) -> list[ShowSearchResult]:
        """Search for TV shows by name. Returns top matches sorted by score."""
        cache_key = query.lower().strip()
        if cache_key in self._show_cache:
            return self._show_cache[cache_key]

        resp = self._get("/search/shows", params={"q": query})
        results = []
        for item in resp.json():
            show = item["show"]
            results.append(
                ShowSearchResult(
                    show_id=show["id"],
                    show_name=show["name"],
                    score=item["score"],
                )
            )

        self._show_cache[cache_key] = results
        return results

    def get_episodes(self, show_id: int) -> list[dict]:
        """Get all episodes for a show."""
        if show_id in self._episode_cache:
            return self._episode_cache[show_id]

        resp = self._get(f"/shows/{show_id}/episodes")
        episodes = resp.json()
        self._episode_cache[show_id] = episodes
        return episodes

    def get_episode_title(
        self, show_name: str, season: int, episode: int
    ) -> EpisodeLookupResult | None:
        """High-level: search show, find episode, return result.

        Returns None if no match found.
        """
        matches = self.search_show(show_name)
        if not matches:
            return None

        # Try the top match first
        best = matches[0]
        result = self._find_episode(best.show_id, best.show_name, season, episode)
        if result:
            return result

        # Try remaining top matches (up to 3)
        for match in matches[1:3]:
            result = self._find_episode(
                match.show_id, match.show_name, season, episode
            )
            if result:
                return result

        return None

    def get_top_matches(
        self, show_name: str, count: int = 3
    ) -> list[ShowSearchResult]:
        """Return top N search matches for interactive selection."""
        matches = self.search_show(show_name)
        return matches[:count]

    def _find_episode(
        self, show_id: int, show_name: str, season: int, episode: int
    ) -> EpisodeLookupResult | None:
        try:
            episodes = self.get_episodes(show_id)
        except requests.HTTPError:
            return None

        for ep in episodes:
            if ep.get("season") == season and ep.get("number") == episode:
                return EpisodeLookupResult(
                    show_name=show_name,
                    episode_title=ep.get("name", ""),
                    season=season,
                    episode=episode,
                    air_date=ep.get("airdate"),
                )
        return None
