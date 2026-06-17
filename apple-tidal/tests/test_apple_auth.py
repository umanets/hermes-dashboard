"""Offline tests for Apple Music auth + adapter mapping.

A throwaway EC P-256 key is generated per-test (mirrors the shape of a MusicKit
.p8 key). No real credentials and no network are used.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from music_service.apple.adapter import AppleMusicAdapter
from music_service.apple.auth import (
    DeveloperTokenProvider,
    MusicUserTokenStore,
    generate_developer_token,
    MAX_TTL_SECONDS,
)
from music_service.config import AppleConfig
from music_service.errors import AuthenticationRequiredError


@pytest.fixture
def ec_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def test_generate_developer_token_claims_and_header(ec_keypair):
    private_pem, public_pem = ec_keypair
    now = int(time.time())
    token = generate_developer_token(
        team_id="TEAM123", key_id="KEY456", private_key=private_pem, issued_at=now
    )
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "KEY456"

    decoded = jwt.decode(token, public_pem, algorithms=["ES256"])
    assert decoded["iss"] == "TEAM123"
    assert decoded["iat"] == now
    assert decoded["exp"] > now


def test_generate_developer_token_rejects_excessive_ttl(ec_keypair):
    private_pem, _ = ec_keypair
    with pytest.raises(ValueError):
        generate_developer_token(
            team_id="T", key_id="K", private_key=private_pem,
            ttl_seconds=MAX_TTL_SECONDS + 1,
        )


def test_generate_developer_token_bad_key_raises_auth_error():
    with pytest.raises(AuthenticationRequiredError):
        generate_developer_token(
            team_id="T", key_id="K", private_key="not a key"
        )


def test_provider_caches_then_refreshes(ec_keypair):
    private_pem, _ = ec_keypair
    provider = DeveloperTokenProvider(
        team_id="T", key_id="K", private_key=private_pem, ttl_seconds=100_000
    )
    t0 = 1_000_000
    a = provider.get_token(now=t0)
    b = provider.get_token(now=t0 + 10)  # within cache window -> same token
    assert a == b
    # Jump past (exp - skew) -> regenerated (different iat -> different token)
    c = provider.get_token(now=t0 + 100_000)
    assert c != a


def test_music_user_token_store_roundtrip(tmp_path):
    store = MusicUserTokenStore(path=tmp_path / "apple_tokens.json")
    assert store.load() is None
    store.save("MUT-abc")
    assert store.load() == "MUT-abc"
    # resolve prefers explicit env token over persisted one
    assert store.resolve("ENV-xyz") == "ENV-xyz"
    assert store.resolve(None) == "MUT-abc"


def _adapter(ec_keypair, tmp_path, mut=None):
    private_pem, _ = ec_keypair
    key_file = tmp_path / "key.p8"
    key_file.write_text(private_pem)
    config = AppleConfig(
        team_id="T", key_id="K", private_key_path=key_file,
        storefront="us", music_user_token=mut,
    )
    store = MusicUserTokenStore(path=tmp_path / "tok.json")
    return AppleMusicAdapter(config, token_store=store)


def test_adapter_capabilities(ec_keypair, tmp_path):
    adapter = _adapter(ec_keypair, tmp_path)
    assert adapter.supports("read_playlists") is True
    assert adapter.supports("search") is True
    assert adapter.supports("create_playlist") is True
    assert adapter.supports("add_tracks") is True


@pytest.mark.asyncio
async def test_adapter_authenticate_without_mut_raises(ec_keypair, tmp_path):
    adapter = _adapter(ec_keypair, tmp_path, mut=None)
    with pytest.raises(AuthenticationRequiredError):
        await adapter.authenticate()


@pytest.mark.asyncio
async def test_adapter_authenticate_with_mut_ok(ec_keypair, tmp_path):
    adapter = _adapter(ec_keypair, tmp_path, mut="MUT-present")
    await adapter.authenticate()  # should not raise




def test_adapter_track_mapping(ec_keypair, tmp_path):
    adapter = _adapter(ec_keypair, tmp_path, mut="MUT")
    raw = {
        "id": "i.123",
        "attributes": {
            "name": "Holy",
            "artistName": "Adam Naas",
            "albumName": "The Love Album",
            "durationInMillis": 240000,
            "isrc": "FRX001",
            "contentRating": "explicit",
            "url": "https://music.apple.com/x",
        },
    }
    track = adapter._map_track(raw)
    assert track.title == "Holy"
    assert track.artist == "Adam Naas"
    assert track.duration_ms == 240000
    assert track.explicit is True
    assert track.isrc == "FRX001"


def test_adapter_playlist_mapping(ec_keypair, tmp_path):
    adapter = _adapter(ec_keypair, tmp_path, mut="MUT")
    raw = {
        "id": "p.1",
        "attributes": {"name": "Discovery", "description": {"standard": "moody"}},
    }
    pl = adapter._map_playlist(raw)
    assert pl.name == "Discovery"
    assert pl.description == "moody"
    assert pl.service == "apple_music"
