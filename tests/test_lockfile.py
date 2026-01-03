import json
import os

import pytest

import takopi.lockfile as lockfile


def test_lockfile_creates_and_cleans_up(tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("ok", encoding="utf-8")

    handle = lockfile.acquire_lock(
        config_path=config_path,
        token_fingerprint="deadbeef",
    )
    try:
        assert lockfile.lock_path_for_config(config_path).exists()
    finally:
        handle.release()

    assert not lockfile.lock_path_for_config(config_path).exists()


def test_lockfile_refuses_running_pid(tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("ok", encoding="utf-8")

    handle = lockfile.acquire_lock(
        config_path=config_path,
        token_fingerprint="deadbeef",
    )
    try:
        with pytest.raises(lockfile.LockError) as exc:
            lockfile.acquire_lock(
                config_path=config_path,
                token_fingerprint="deadbeef",
            )
        message = str(exc.value).lower()
        assert "already running" in message
        assert str(lockfile.lock_path_for_config(config_path)) in str(exc.value)
    finally:
        handle.release()


def test_lockfile_replaces_dead_pid(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("ok", encoding="utf-8")
    lock_path = lockfile.lock_path_for_config(config_path)
    payload = {"pid": 424242, "token_fingerprint": "deadbeef"}
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(lockfile, "_pid_running", lambda pid: False)

    handle = lockfile.acquire_lock(
        config_path=config_path,
        token_fingerprint="deadbeef",
    )
    try:
        updated = json.loads(lock_path.read_text(encoding="utf-8"))
        assert updated["pid"] == os.getpid()
        assert updated["token_fingerprint"] == "deadbeef"
    finally:
        handle.release()


def test_lockfile_replaces_token_mismatch(tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("ok", encoding="utf-8")
    lock_path = lockfile.lock_path_for_config(config_path)
    payload = {"pid": os.getpid(), "token_fingerprint": "other"}
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    handle = lockfile.acquire_lock(
        config_path=config_path,
        token_fingerprint="deadbeef",
    )
    try:
        updated = json.loads(lock_path.read_text(encoding="utf-8"))
        assert updated["token_fingerprint"] == "deadbeef"
    finally:
        handle.release()
