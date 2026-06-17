"""Offline tests for Apple Music write endpoints (mocked transport)."""

import json

import httpx
import pytest

from music_service.apple.client import AppleMusicClient


def _client(http):
    return AppleMusicClient(
        developer_token="dev", music_user_token="mut", storefront="us", http=http
    )


@pytest.mark.asyncio
async def test_create_library_playlist_posts_correct_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["has_mut"] = "Music-User-Token" in request.headers
        return httpx.Response(201, json={"data": [{"id": "p.123", "attributes": {"name": "Hermes Integration Test"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.music.apple.com") as http:
        client = _client(http)
        result = await client.create_library_playlist(
            "Hermes Integration Test", description="x", track_ids=["1", "2"]
        )

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/me/library/playlists"
    assert seen["has_mut"] is True
    assert seen["body"]["attributes"]["name"] == "Hermes Integration Test"
    assert seen["body"]["relationships"]["tracks"]["data"] == [
        {"id": "1", "type": "songs"},
        {"id": "2", "type": "songs"},
    ]
    assert result["data"][0]["id"] == "p.123"


@pytest.mark.asyncio
async def test_create_library_playlist_omits_relationships_without_tracks():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": [{"id": "p.1", "attributes": {"name": "n"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.music.apple.com") as http:
        await _client(http).create_library_playlist("n")
    assert "relationships" not in seen["body"]


@pytest.mark.asyncio
async def test_add_playlist_tracks_posts_and_handles_204():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.music.apple.com") as http:
        await _client(http).add_playlist_tracks("p.123", ["10", "11"])

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/me/library/playlists/p.123/tracks"
    assert seen["body"]["data"] == [
        {"id": "10", "type": "songs"},
        {"id": "11", "type": "songs"},
    ]
