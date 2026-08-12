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
        # The message shortens paths under $HOME to ~/…, and pytest's tmp_path
        # lives under $HOME on Windows — so compare against the displayed form
        # rather than the absolute one.
        lock_path = lockfile.lock_path_for_config(config_path)
        assert lockfile._display_lock_path(lock_path) in str(exc.value)
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


def test_lockfile_rewrites_token_of_a_stale_lock(tmp_path) -> None:
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


def test_lockfile_refuses_second_instance_even_with_a_foreign_live_pid(
    tmp_path, monkeypatch
) -> None:
    """A live holder must win regardless of what the file claims.

    The pid in the file is only a hint. Pretending every recorded pid is dead —
    which is what a non-elevated process saw when an elevated one held the
    lock, and what a recycled pid produces — must not let a second instance in.
    """
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("ok", encoding="utf-8")

    handle = lockfile.acquire_lock(
        config_path=config_path,
        token_fingerprint="deadbeef",
    )
    try:
        monkeypatch.setattr(lockfile, "_pid_running", lambda pid: False)
        with pytest.raises(lockfile.LockError):
            lockfile.acquire_lock(
                config_path=config_path,
                token_fingerprint="deadbeef",
            )
    finally:
        handle.release()


def test_lockfile_refuses_second_instance_on_token_mismatch(tmp_path) -> None:
    """A different token must not hand the lock to a second live instance.

    Taking the lock over on a fingerprint mismatch used to bypass the liveness
    check entirely, which is a second way to end up with two bridges running.
    """
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("ok", encoding="utf-8")

    handle = lockfile.acquire_lock(config_path=config_path, token_fingerprint="aaa")
    try:
        with pytest.raises(lockfile.LockError):
            lockfile.acquire_lock(config_path=config_path, token_fingerprint="bbb")
    finally:
        handle.release()


def test_lockfile_message_names_the_holder(tmp_path) -> None:
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
        message = str(exc.value)
        assert "already running" in message
        assert f"pid {os.getpid()}" in message
        assert exc.value.holder_pid == os.getpid()
    finally:
        handle.release()


def test_lockfile_frees_the_os_lock_on_release(tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("ok", encoding="utf-8")

    first = lockfile.acquire_lock(config_path=config_path, token_fingerprint="x")
    first.release()

    second = lockfile.acquire_lock(config_path=config_path, token_fingerprint="x")
    try:
        payload = json.loads(
            lockfile.lock_path_for_config(config_path).read_text(encoding="utf-8")
        )
        assert payload["pid"] == os.getpid()
    finally:
        second.release()


def test_lockfile_ignores_a_stale_file_without_an_os_lock(tmp_path) -> None:
    """A leftover file from a crashed run must not block a fresh start."""
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("ok", encoding="utf-8")
    lock_path = lockfile.lock_path_for_config(config_path)
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "token_fingerprint": "deadbeef"}),
        encoding="utf-8",
    )

    handle = lockfile.acquire_lock(
        config_path=config_path,
        token_fingerprint="deadbeef",
    )
    try:
        assert lock_path.exists()
    finally:
        handle.release()
