"""Encryption at rest, transactional upgrades and safe credential failures."""

import os
import stat
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from web.credential_crypto import (
    CIPHERTEXT_PREFIX,
    CredentialEncryptionError,
    decrypt_api_key,
    encrypt_api_key,
    key_file,
)
from web.database import initialize_database
from web.models import User, UserApiProfile
from web.routers import images

ROOT = "/photodream/api/settings/api-profiles"


def profile_record():
    return SimpleNamespace(
        id="profile-id", user_id="user-id", base_url="https://relay.example/v1"
    )


def test_authenticated_encryption_is_randomized_and_reversible():
    cipher = Fernet(Fernet.generate_key())
    profile = profile_record()
    first = encrypt_api_key(profile, "private-接口-key", cipher)
    second = encrypt_api_key(profile, "private-接口-key", cipher)
    assert first != second and first.startswith(CIPHERTEXT_PREFIX)
    assert "private-接口-key" not in first
    profile.api_key = first
    assert decrypt_api_key(profile, cipher) == "private-接口-key"
    with pytest.raises(CredentialEncryptionError):
        decrypt_api_key(profile, Fernet(Fernet.generate_key()))


@pytest.mark.parametrize("field", ["user_id", "id", "base_url"])
def test_ciphertext_cannot_be_moved_to_another_owner_profile_or_url(field):
    cipher = Fernet(Fernet.generate_key())
    profile = profile_record()
    profile.api_key = encrypt_api_key(profile, "private-key", cipher)
    setattr(profile, field, "tampered-value")
    with pytest.raises(CredentialEncryptionError, match="解密失败"):
        decrypt_api_key(profile, cipher)


@pytest.mark.parametrize("value", ["plaintext-key", CIPHERTEXT_PREFIX + "corrupted"])
def test_plaintext_and_corrupted_tokens_cannot_be_used_at_runtime(value):
    profile = profile_record()
    profile.api_key = value
    with pytest.raises(CredentialEncryptionError):
        decrypt_api_key(profile, Fernet(Fernet.generate_key()))


def test_plaintext_upgrade_persists_key_and_is_idempotent(client, environment):
    path, engine = environment
    with Session(engine) as session, session.begin():
        owner = session.scalar(select(User.id))
        for index in range(2):
            session.add(
                UserApiProfile(
                    id=f"legacy-{index}",
                    user_id=owner,
                    name=f"legacy-{index}",
                    base_url="https://relay.example/v1",
                    api_key=f"legacy-secret-{index}",
                )
            )
    # Simulate the first upgrade from an installation with only plaintext keys.
    key_file().unlink()
    initialize_database()
    original_key = key_file().read_bytes()
    assert key_file() == path / "secrets" / "api-keys.key"
    if os.name == "posix":
        assert stat.S_IMODE(key_file().stat().st_mode) == 0o600
        assert stat.S_IMODE(key_file().parent.stat().st_mode) == 0o700
    with Session(engine) as session:
        encrypted = {p.id: p.api_key for p in session.scalars(select(UserApiProfile))}
        for profile in session.scalars(select(UserApiProfile)):
            assert profile.api_key.startswith(CIPHERTEXT_PREFIX)
            assert decrypt_api_key(profile) == "legacy-secret-" + profile.id[-1]
    initialize_database()
    assert key_file().read_bytes() == original_key
    with Session(engine) as session:
        assert {
            p.id: p.api_key for p in session.scalars(select(UserApiProfile))
        } == encrypted


@pytest.mark.parametrize("failure", ["missing", "wrong", "invalid", "corrupted"])
def test_bad_keys_fail_startup_and_generation_without_fallback(
    client, environment, monkeypatch, failure
):
    response = client.post(
        ROOT,
        json={
            "name": "private",
            "base_url": "https://relay.example/v1",
            "api_key": "private-secret",
        },
    )
    assert response.status_code == 201
    profile_id = response.json()["id"]
    with Session(environment[1]) as session, session.begin():
        profile = session.get(UserApiProfile, profile_id)
        if failure == "corrupted":
            profile.api_key = CIPHERTEXT_PREFIX + "broken-token"
        stored = profile.api_key
    if failure == "missing":
        key_file().unlink()
    elif failure == "wrong":
        monkeypatch.setenv("PHOTODREAM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    elif failure == "invalid":
        monkeypatch.setenv("PHOTODREAM_ENCRYPTION_KEY", "invalid-key")

    with pytest.raises(CredentialEncryptionError) as error:
        initialize_database()
    assert "private-secret" not in str(error.value) and stored not in str(error.value)
    if failure == "missing":
        assert not key_file().exists()
    with Session(environment[1]) as session:
        assert session.get(UserApiProfile, profile_id).api_key == stored

    def must_not_call_relay(**kwargs):
        pytest.fail("Credential failure must never call an upstream API")

    monkeypatch.setattr(images, "relay_generate_raw", must_not_call_relay)
    response = client.post(
        "/photodream/api/generate",
        data={
            "prompt": "test",
            "api_profile_id": profile_id,
            "api_key": "fallback-key",
            "base_url": "https://fallback.example/v1",
        },
    )
    assert response.status_code == 503, response.text
    assert "private-secret" not in response.text and stored not in response.text
    # Metadata stays accessible, so a damaged saved configuration can be removed.
    assert client.get(ROOT).status_code == 200
    assert client.delete(ROOT + "/" + profile_id).status_code == 200


def test_invalid_ciphertext_rolls_back_plaintext_migration(client, environment):
    with Session(environment[1]) as session, session.begin():
        owner = session.scalar(select(User.id))
        for profile_id, value in [
            ("plaintext", "legacy-secret"),
            ("broken", CIPHERTEXT_PREFIX + "broken"),
        ]:
            session.add(
                UserApiProfile(
                    id=profile_id,
                    user_id=owner,
                    name=profile_id,
                    base_url="https://relay.example/v1",
                    api_key=value,
                )
            )
    with pytest.raises(CredentialEncryptionError):
        initialize_database()
    with Session(environment[1]) as session:
        assert session.get(UserApiProfile, "plaintext").api_key == "legacy-secret"


def test_environment_key_avoids_local_file_and_is_required_on_restart(
    environment, monkeypatch
):
    monkeypatch.setenv("PHOTODREAM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    initialize_database()
    assert not key_file().exists()
    profile = profile_record()
    profile.api_key = encrypt_api_key(profile, "env-key-secret")
    assert decrypt_api_key(profile) == "env-key-secret"
    monkeypatch.delenv("PHOTODREAM_ENCRYPTION_KEY")
    with pytest.raises(CredentialEncryptionError):
        decrypt_api_key(profile)


def test_custom_key_file_is_persistent(environment, monkeypatch):
    target = environment[0] / "private" / "custom.key"
    monkeypatch.setenv("PHOTODREAM_ENCRYPTION_KEY_FILE", str(target))
    initialize_database()
    original_key = target.read_bytes()
    initialize_database()
    assert target.read_bytes() == original_key
    assert not (environment[0] / "secrets" / "api-keys.key").exists()
