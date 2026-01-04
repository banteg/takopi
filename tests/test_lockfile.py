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


def test_lock_handle_context_manager(tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("ok", encoding="utf-8")
    lock_path = lockfile.lock_path_for_config(config_path)

    with lockfile.acquire_lock(config_path=config_path) as handle:
        assert lock_path.exists()
        assert handle.path == lock_path

    assert not lock_path.exists()


def test_token_fingerprint() -> None:
    fp1 = lockfile.token_fingerprint("test-token")
    fp2 = lockfile.token_fingerprint("test-token")
    fp3 = lockfile.token_fingerprint("other-token")

    assert fp1 == fp2
    assert fp1 != fp3
    assert len(fp1) == 10


def test_lock_info_dataclass() -> None:
    info = lockfile.LockInfo(pid=123, token_fingerprint="abc123")
    assert info.pid == 123
    assert info.token_fingerprint == "abc123"


def test_lock_error_properties(tmp_path) -> None:
    lock_path = tmp_path / "test.lock"
    err = lockfile.LockError(path=lock_path, state="test-state")
    assert err.path == lock_path
    assert err.state == "test-state"


def test_read_lock_info_malformed_json(tmp_path) -> None:
    lock_path = tmp_path / "test.lock"
    lock_path.write_text("not json", encoding="utf-8")

    result = lockfile._read_lock_info(lock_path)
    assert result is None


def test_read_lock_info_not_dict(tmp_path) -> None:
    lock_path = tmp_path / "test.lock"
    lock_path.write_text("[]", encoding="utf-8")

    result = lockfile._read_lock_info(lock_path)
    assert result is None


def test_read_lock_info_invalid_pid(tmp_path) -> None:
    lock_path = tmp_path / "test.lock"
    lock_path.write_text(
        '{"pid": "not-int", "token_fingerprint": "abc"}', encoding="utf-8"
    )

    result = lockfile._read_lock_info(lock_path)
    assert result is not None
    assert result.pid is None
    assert result.token_fingerprint == "abc"


def test_read_lock_info_boolean_pid(tmp_path) -> None:
    lock_path = tmp_path / "test.lock"
    lock_path.write_text('{"pid": true, "token_fingerprint": "abc"}', encoding="utf-8")

    result = lockfile._read_lock_info(lock_path)
    assert result is not None
    assert result.pid is None


def test_read_lock_info_invalid_token_fingerprint(tmp_path) -> None:
    lock_path = tmp_path / "test.lock"
    lock_path.write_text('{"pid": 123, "token_fingerprint": 456}', encoding="utf-8")

    result = lockfile._read_lock_info(lock_path)
    assert result is not None
    assert result.pid == 123
    assert result.token_fingerprint is None


def test_pid_running_none() -> None:
    assert lockfile._pid_running(None) is False


def test_pid_running_zero() -> None:
    assert lockfile._pid_running(0) is False


def test_pid_running_negative() -> None:
    assert lockfile._pid_running(-1) is False


def test_pid_running_nonexistent() -> None:
    assert lockfile._pid_running(999999) is False


def test_pid_running_current() -> None:
    assert lockfile._pid_running(os.getpid()) is True


def test_format_lock_message_running(tmp_path) -> None:
    lock_path = tmp_path / "test.lock"
    msg = lockfile._format_lock_message(lock_path, "running")
    assert "already running" in msg
    assert "remove" in msg


def test_format_lock_message_other_state(tmp_path) -> None:
    lock_path = tmp_path / "test.lock"
    msg = lockfile._format_lock_message(lock_path, "some error")
    assert "lock failed" in msg
    assert "some error" in msg
