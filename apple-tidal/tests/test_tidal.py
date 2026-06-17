"""Offline tests for the TIDAL adapter, auth, and client (no network, no creds)."""

import base64
import hashlib
import json

import httpx
import pytest

from music_service.config import TidalConfig
from music_service.errors import CapabilityNotSupportedError
from music_service.tidal.adapter import TidalAdapter, parse_iso8601_duration_ms
from music_service.tidal.auth import (
    TidalTokens,
    TidalTokenStore,
    _tokens_from_response,
    build_authorize_url,
    generate_pkce_pair,
)
from music_service.tidal.client import TidalClient


def _config():
    return TidalConfig(
        client_id="cid",
        client_secret="secret",
        redirect_uri="http://127.0.0.1:8765/callback",
        country="US",
    )


# ---- PKCE / authorize URL -------------------------------------------------

def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = generate_pkce_pair(verifier_bytes=b"\x00" * 64)
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in verifier and "=" not in challenge  # base64url, no padding


def test_authorize_url_has_required_params():
    url = build_authorize_url(
        client_id="cid",
        redirect_uri="http://127.0.0.1:8765/callback",
        scopes=("playlists.read", "playlists.write"),
        code_challenge="chal",
        state="xyz",
    )
    assert url.startswith("https://login.tidal.com/authorize?")
    for frag in (
        "response_type=code",
        "client_id=cid",
        "code_challenge=chal",
        "code_challenge_method=S256",
        "state=xyz",
        "playlists.read",
        "playlists.write",
    ):
        assert frag in url


# ---- token parsing / store ------------------------------------------------

def test_tokens_from_response_extracts_user_id_and_scope():
    t = _tokens_from_response(
        {
            "access_token": "a",
            "refresh_token": "r",
            "expires_in": 3600,
            "scope": "playlists.read playlists.write",
            "user_id": 12345,
        }
    )
    assert t.user_id == "12345"
    assert "playlists.write" in t.scope
    assert not t.is_expired()


def test_refresh_response_keeps_previous_refresh_token():
    prev = TidalTokens(access_token="old", refresh_token="keepme", expires_at=0, user_id="1")
    new = _tokens_from_response({"access_token": "new", "expires_in": 100}, previous=prev)
    assert new.refresh_token == "keepme"
    assert new.user_id == "1"


def test_tokens_is_expired_skew():
    t = TidalTokens(access_token="a", refresh_token="r", expires_at=1000)
    assert t.is_expired(now=1000) is True
    assert t.is_expired(now=950) is True  # within 60s refresh skew
    assert t.is_expired(now=800) is False


def test_token_store_roundtrip(tmp_path):
    store = TidalTokenStore(path=tmp_path / "tidal.json")
    assert store.load() is None
    store.save(TidalTokens(access_token="a", refresh_token="r", expires_at=123.0, scope="s", user_id="9"))
    loaded = store.load()
    assert loaded.access_token == "a"
    assert loaded.user_id == "9"


# ---- duration parsing -----------------------------------------------------

@pytest.mark.parametrize(
    "iso,ms",
    [("PT3M20S", 200000), ("PT1H2M3S", 3723000), ("PT45S", 45000), (None, None), ("bad", None)],
)
def test_parse_iso8601_duration(iso, ms):
    assert parse_iso8601_duration_ms(iso) == ms


# ---- mapping --------------------------------------------------------------

def _adapter(tmp_path):
    return TidalAdapter(_config(), token_store=TidalTokenStore(path=tmp_path / "t.json"))


def test_map_playlist(tmp_path):
    a = _adapter(tmp_path)
    pl = a._map_playlist({"id": "p1", "attributes": {"name": "Moody", "numberOfItems": 7}})
    assert pl.name == "Moody"
    assert pl.track_count == 7
    assert pl.service == "tidal"


def test_map_tracks_resolves_artist_from_included(tmp_path):
    a = _adapter(tmp_path)
    data = [{"type": "tracks", "id": "t1"}]
    included = [
        {
            "type": "tracks",
            "id": "t1",
            "attributes": {"title": "Strong", "isrc": "GB123", "duration": "PT4M55S", "explicit": False},
            "relationships": {
                "artists": {"data": [{"type": "artists", "id": "a1"}]},
                "albums": {"data": [{"type": "albums", "id": "al1"}]},
            },
        },
        {"type": "artists", "id": "a1", "attributes": {"name": "London Grammar"}},
        {"type": "albums", "id": "al1", "attributes": {"title": "If You Wait"}},
    ]
    tracks = a._map_tracks_from(data, included)
    assert len(tracks) == 1
    assert tracks[0].artist == "London Grammar"
    assert tracks[0].album == "If You Wait"
    assert tracks[0].duration_ms == 295000
    assert tracks[0].isrc == "GB123"


# ---- write capability gating ---------------------------------------------

@pytest.mark.asyncio
async def test_create_playlist_blocked_without_write_scope(tmp_path):
    a = _adapter(tmp_path)
    a._tokens = TidalTokens(access_token="a", refresh_token="r", expires_at=9e9, scope="playlists.read", user_id="1")
    assert a.has_write_scope() is False
    with pytest.raises(CapabilityNotSupportedError):
        await a.create_playlist("Hermes Integration Test")


def test_has_write_scope_true(tmp_path):
    a = _adapter(tmp_path)
    a._tokens = TidalTokens(access_token="a", refresh_token="r", expires_at=9e9, scope="playlists.read playlists.write", user_id="1")
    assert a.has_write_scope() is True


@pytest.mark.asyncio
async def test_add_to_favorites_requires_collection_write(tmp_path):
    a = _adapter(tmp_path)
    a._tokens = TidalTokens(access_token="a", refresh_token="r", expires_at=9e9, scope="playlists.write", user_id="1")
    assert a.has_collection_write_scope() is False
    with pytest.raises(CapabilityNotSupportedError):
        await a.add_to_library(["t1"])


# ---- client chunking (mocked transport) ----------------------------------

@pytest.mark.asyncio
async def test_add_tracks_chunks_at_50():
    posted_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted_bodies.append(json.loads(request.content))
        return httpx.Response(201, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://openapi.tidal.com/v2") as http:
        client = TidalClient(access_token="tok", country="US", http=http)
        ids = [str(i) for i in range(120)]
        await client.add_tracks("p1", ids)

    assert [len(b["data"]) for b in posted_bodies] == [50, 50, 20]
    assert posted_bodies[0]["data"][0] == {"id": "0", "type": "tracks"}


@pytest.mark.asyncio
async def test_client_403_maps_to_capability_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": [{"detail": "insufficient scope"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://openapi.tidal.com/v2") as http:
        client = TidalClient(access_token="tok", http=http)
        with pytest.raises(CapabilityNotSupportedError):
            await client.create_playlist("x")


# ---- 429 retry / backoff --------------------------------------------------

@pytest.mark.asyncio
async def test_429_then_success_retries_with_backoff():
    calls = {"n": 0}
    slept = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={})
        return httpx.Response(200, json={"data": [], "included": []})

    async def fake_sleep(s):
        slept.append(s)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://openapi.tidal.com/v2") as http:
        client = TidalClient(access_token="tok", http=http, sleeper=fake_sleep)
        data, _ = await client.get_user_playlists("1")
    assert calls["n"] == 3  # two 429s + one success
    assert slept == [2.0, 2.0]  # honored Retry-After each retry


@pytest.mark.asyncio
async def test_429_exhausted_raises_rate_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    async def fake_sleep(s):
        pass

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://openapi.tidal.com/v2") as http:
        client = TidalClient(access_token="tok", http=http, max_retries=2, sleeper=fake_sleep)
        from music_service.errors import RateLimitError

        with pytest.raises(RateLimitError):
            await client.get_user_playlists("1")


# ---- bounded pagination (the search 429 fix) ------------------------------

@pytest.mark.asyncio
async def test_search_stops_at_max_items_and_does_not_fetch_all_pages():
    pages_fetched = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        pages_fetched["n"] += 1
        # Always advertise a next cursor -> would loop forever if unbounded.
        return httpx.Response(
            200,
            json={
                "data": [{"type": "tracks", "id": f"{pages_fetched['n']}"}],
                "included": [],
                "links": {"next": "/x?page%5Bcursor%5D=more"},
            },
        )

    async def fake_sleep(s):
        pass

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://openapi.tidal.com/v2") as http:
        client = TidalClient(access_token="tok", http=http, sleeper=fake_sleep)
        data, _ = await client.search_tracks("anything", max_items=3)
    assert len(data) == 3
    assert pages_fetched["n"] == 3  # stopped at max_items, not infinite


@pytest.mark.asyncio
async def test_delete_playlist_issues_delete(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://openapi.tidal.com/v2") as http:
        client = TidalClient(access_token="tok", http=http)
        await client.delete_playlist("abc")
    assert seen["method"] == "DELETE"
    assert seen["path"].endswith("/playlists/abc")
