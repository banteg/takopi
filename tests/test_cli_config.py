from pathlib import Path
import tomllib

from typer.testing import CliRunner

from takopi import cli


def _write_min_config(path: Path) -> None:
    path.write_text(
        'transport = "telegram"\n'
        "\n"
        "[transports.telegram]\n"
        'bot_token = "token"\n'
        "chat_id = 123\n",
        encoding="utf-8",
    )


def test_config_list_outputs_flattened(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n'
        "watch_config = true\n"
        "\n"
        "[transports.telegram]\n"
        'bot_token = "token"\n'
        "chat_id = 123\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.create_app(),
        ["config", "list", "--config-path", str(config_path)],
    )

    assert result.exit_code == 0
    lines = [line.strip() for line in result.output.splitlines() if line.strip()]
    assert 'transport = "telegram"' in lines
    assert "watch_config = true" in lines
    assert 'transports.telegram.bot_token = "token"' in lines
    assert "transports.telegram.chat_id = 123" in lines
    assert not any(line.startswith("default_engine") for line in lines)


def test_config_get_outputs_literal_and_table_error(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    _write_min_config(config_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.create_app(),
        [
            "config",
            "get",
            "transports.telegram.chat_id",
            "--config-path",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    assert result.output.strip() == "123"

    result = runner.invoke(
        cli.create_app(),
        [
            "config",
            "get",
            "transports.telegram",
            "--config-path",
            str(config_path),
        ],
    )

    assert result.exit_code == 2
    assert "table" in result.output


def test_config_get_missing_key(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    _write_min_config(config_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.create_app(),
        ["config", "get", "nope", "--config-path", str(config_path)],
    )

    assert result.exit_code == 1
    assert result.output == ""


def test_config_set_parses_and_writes(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    _write_min_config(config_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.create_app(),
        ["config", "set", "watch_config", "true", "--config-path", str(config_path)],
    )
    assert result.exit_code == 0

    result = runner.invoke(
        cli.create_app(),
        [
            "config",
            "set",
            "default_engine",
            "openai",
            "--config-path",
            str(config_path),
        ],
    )
    assert result.exit_code == 0

    result = runner.invoke(
        cli.create_app(),
        [
            "config",
            "set",
            "watch_config",
            "False",
            "--config-path",
            str(config_path),
        ],
    )
    assert result.exit_code == 0

    result = runner.invoke(
        cli.create_app(),
        [
            "config",
            "set",
            "transports.telegram.chat_id",
            "456",
            "--config-path",
            str(config_path),
        ],
    )
    assert result.exit_code == 0

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert data["watch_config"] is False
    assert data["default_engine"] == "openai"
    assert data["transports"]["telegram"]["chat_id"] == 456


def test_config_unset_prunes_tables(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n'
        "\n"
        "[transports.telegram]\n"
        'bot_token = "token"\n'
        "chat_id = 123\n"
        "\n"
        "[projects.foo]\n"
        'path = "/tmp/repo"\n',
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.create_app(),
        ["config", "unset", "projects.foo", "--config-path", str(config_path)],
    )

    assert result.exit_code == 0
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "projects" not in data


def test_config_set_schema_validation_error(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n'
        "\n"
        "[transports.telegram]\n"
        'bot_token = "token"\n'
        "chat_id = 123\n"
        "\n"
        "[projects.foo]\n"
        'path = "/tmp/repo"\n',
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.create_app(),
        [
            "config",
            "set",
            "projects.foo.extra",
            "nope",
            "--config-path",
            str(config_path),
        ],
    )

    assert result.exit_code == 2
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "extra" not in data.get("projects", {}).get("foo", {})
