import json
import socket

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


def test_lockfile_conflict_mentions_lock_path(tmp_path) -> None:
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
        assert str(lockfile.lock_path_for_config(config_path)) in str(exc.value)
    finally:
        handle.release()


def test_lockfile_reports_stale_pid(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("ok", encoding="utf-8")
    lock_path = lockfile.lock_path_for_config(config_path)
    payload = {
        "version": 1,
        "instance_id": "old",
        "pid": 424242,
        "hostname": socket.gethostname(),
    }
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(lockfile, "_pid_state", lambda pid: "not_running")

    with pytest.raises(lockfile.LockError) as exc:
        lockfile.acquire_lock(
            config_path=config_path,
            token_fingerprint="deadbeef",
        )

    message = str(exc.value).lower()
    assert "delete" in message
    assert str(lock_path) in str(exc.value)
