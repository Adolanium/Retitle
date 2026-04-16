from unittest.mock import MagicMock, patch

import pytest

from retitle.api.opensubtitles import (
    OpenSubtitlesClient,
    SubtitleDownloadResult,
    SubtitleSearchResult,
)


def _make_search_response(items):
    """Build a mock API response for /subtitles."""
    data = []
    for file_id, release, count, trusted in items:
        data.append({
            "id": file_id,
            "attributes": {
                "language": "en",
                "release": release,
                "download_count": count,
                "from_trusted": trusted,
                "files": [{"file_id": file_id}],
            },
        })
    return {"data": data}


@patch.dict("os.environ", {
    "OPENSUBTITLES_API_KEY": "test-key",
    "OPENSUBTITLES_USERNAME": "testuser",
    "OPENSUBTITLES_PASSWORD": "testpass",
})
class TestOpenSubtitlesClient:

    def _make_client(self):
        client = OpenSubtitlesClient()
        client._session = MagicMock()
        client._last_request_time = 0.0
        return client

    def test_search_returns_sorted_results(self):
        client = self._make_client()
        response = MagicMock()
        response.json.return_value = _make_search_response([
            (1, "release.A", 100, False),
            (2, "release.B", 5000, True),
            (3, "release.C", 500, False),
        ])
        response.raise_for_status = MagicMock()
        client._session.get.return_value = response

        results = client.search("Breaking Bad", season_number=1, episode_number=1)
        assert len(results) == 3
        assert results[0].download_count == 5000
        assert results[1].download_count == 500
        assert results[2].download_count == 100

    def test_search_caching(self):
        client = self._make_client()
        response = MagicMock()
        response.json.return_value = _make_search_response([
            (1, "release.A", 100, False),
        ])
        response.raise_for_status = MagicMock()
        client._session.get.return_value = response

        results1 = client.search("Test Show", season_number=1, episode_number=1)
        results2 = client.search("Test Show", season_number=1, episode_number=1)

        assert results1 == results2
        # Only one HTTP call despite two searches
        assert client._session.get.call_count == 1

    def test_search_with_movie_params(self):
        client = self._make_client()
        response = MagicMock()
        response.json.return_value = _make_search_response([])
        response.raise_for_status = MagicMock()
        client._session.get.return_value = response

        client.search("Inception", year=2010, media_type="movie")

        call_args = client._session.get.call_args
        params = call_args[1].get("params") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["params"]
        assert params["query"] == "Inception"
        assert params["year"] == 2010
        assert params["type"] == "movie"

    def test_search_with_episode_params(self):
        client = self._make_client()
        response = MagicMock()
        response.json.return_value = _make_search_response([])
        response.raise_for_status = MagicMock()
        client._session.get.return_value = response

        client.search(
            "Breaking Bad",
            season_number=1,
            episode_number=1,
            media_type="episode",
        )

        call_args = client._session.get.call_args
        params = call_args[1].get("params") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["params"]
        assert params["season_number"] == 1
        assert params["episode_number"] == 1
        assert params["type"] == "episode"

    def test_login_called_on_first_download(self):
        client = self._make_client()
        # Mock login response
        login_resp = MagicMock()
        login_resp.json.return_value = {"token": "jwt-token-123"}
        login_resp.raise_for_status = MagicMock()

        # Mock download response
        dl_resp = MagicMock()
        dl_resp.json.return_value = {
            "link": "https://example.com/sub.srt",
            "file_name": "subtitle.srt",
            "remaining": 19,
        }
        dl_resp.raise_for_status = MagicMock()

        client._session.post.side_effect = [login_resp, dl_resp]

        result = client.download(file_id=12345)
        assert result.download_url == "https://example.com/sub.srt"
        assert result.remaining == 19
        assert client._session.post.call_count == 2  # login + download

    def test_token_reused_within_expiry(self):
        client = self._make_client()
        client._token = "existing-token"
        client._token_expiry = 9999999999.0  # Far future

        dl_resp = MagicMock()
        dl_resp.json.return_value = {
            "link": "https://example.com/sub.srt",
            "file_name": "subtitle.srt",
            "remaining": 19,
        }
        dl_resp.raise_for_status = MagicMock()
        client._session.post.return_value = dl_resp

        client.download(file_id=12345)
        # Only download call, no login
        assert client._session.post.call_count == 1

    def test_empty_search_results(self):
        client = self._make_client()
        response = MagicMock()
        response.json.return_value = {"data": []}
        response.raise_for_status = MagicMock()
        client._session.get.return_value = response

        results = client.search("nonexistent show xyz")
        assert results == []


def test_missing_api_key_raises():
    with patch("retitle.api.opensubtitles.load_dotenv"), \
         patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="API key not configured"):
            OpenSubtitlesClient()


def test_missing_credentials_raises_on_download():
    with patch("retitle.api.opensubtitles.load_dotenv"), \
         patch.dict("os.environ", {"OPENSUBTITLES_API_KEY": "test-key"}, clear=True):
        client = OpenSubtitlesClient()
        client._session = MagicMock()
        with pytest.raises(ValueError, match="username/password not configured"):
            client.download(file_id=123)
