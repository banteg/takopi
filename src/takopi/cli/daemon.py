from __future__ import annotations

import os
import subprocess
from pathlib import Path

import typer


def _get_takopi_executable() -> str:
    """Get the takopi command for systemd.

    Uses just 'takopi' so systemd finds it via PATH at runtime,
    rather than hardcoding the current environment's path.
    """
    return "takopi"


def _get_systemd_user_dir() -> Path:
    """Get the systemd user unit directory."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / "systemd" / "user"
    return Path.home() / ".config" / "systemd" / "user"


def _generate_service_unit(
    *,
    exec_path: str,
    description: str = "Takopi Telegram Bridge",
    working_dir: str | None = None,
) -> str:
    """Generate a systemd service unit file content."""
    home = str(Path.home())
    # Use the current PATH to capture nvm, cargo, and other tool paths
    path_value = os.environ.get("PATH", "")
    if not path_value:
        path_dirs = [
            f"{home}/.local/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        path_value = ":".join(path_dirs)

    lines = [
        "[Unit]",
        f"Description={description}",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"Environment=HOME={home}",
        f"Environment=PATH={path_value}",
        "Environment=TAKOPI_NO_INTERACTIVE=1",
        f"ExecStart=/bin/sh -c 'exec {exec_path}'",
        "Restart=on-failure",
        "RestartSec=10",
    ]

    if working_dir:
        lines.append(f"WorkingDirectory={working_dir}")

    lines.extend([
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])

    return "\n".join(lines)


def _run_systemctl(args: list[str], *, check: bool = True) -> bool:
    """Run a systemctl command for user services."""
    cmd = ["systemctl", "--user", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return not (check and result.returncode != 0)
    except FileNotFoundError:
        return False


def daemon_install(
    enable: bool = typer.Option(
        False,
        "--enable",
        help="Enable the service to start on boot.",
    ),
    start: bool = typer.Option(
        False,
        "--start",
        help="Start the service immediately after installation.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing service file.",
    ),
) -> None:
    """Install takopi as a systemd user service."""
    systemd_dir = _get_systemd_user_dir()
    service_path = systemd_dir / "takopi.service"

    if service_path.exists() and not force:
        typer.echo(f"Service file already exists at {service_path}", err=True)
        typer.echo("Use --force to overwrite.", err=True)
        raise typer.Exit(code=1)

    exec_path = _get_takopi_executable()
    service_content = _generate_service_unit(exec_path=exec_path)

    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_path.write_text(service_content)
    typer.echo(f"Created service file: {service_path}")

    if not _run_systemctl(["daemon-reload"]):
        typer.echo("warning: failed to reload systemd daemon", err=True)

    if enable:
        if _run_systemctl(["enable", "takopi.service"]):
            typer.echo("Enabled takopi.service")
        else:
            typer.echo("warning: failed to enable service", err=True)

    if start:
        if _run_systemctl(["start", "takopi.service"]):
            typer.echo("Started takopi.service")
        else:
            typer.echo("warning: failed to start service", err=True)

    typer.echo("")
    typer.echo("Usage:")
    typer.echo("  systemctl --user start takopi     # Start the service")
    typer.echo("  systemctl --user stop takopi      # Stop the service")
    typer.echo("  systemctl --user restart takopi   # Restart the service")
    typer.echo("  systemctl --user status takopi    # Check status")
    typer.echo("  journalctl --user -u takopi -f    # View logs")


def daemon_uninstall(
    stop: bool = typer.Option(
        True,
        "--stop/--no-stop",
        help="Stop the service before uninstalling.",
    ),
) -> None:
    """Uninstall the takopi systemd user service."""
    systemd_dir = _get_systemd_user_dir()
    service_path = systemd_dir / "takopi.service"

    if not service_path.exists():
        typer.echo("Service file not found.", err=True)
        raise typer.Exit(code=1)

    if stop:
        _run_systemctl(["stop", "takopi.service"], check=False)
        _run_systemctl(["disable", "takopi.service"], check=False)

    service_path.unlink()
    typer.echo(f"Removed service file: {service_path}")

    _run_systemctl(["daemon-reload"])
    typer.echo("Uninstalled takopi.service")


def daemon_status() -> None:
    """Show the status of the takopi systemd service."""
    result = subprocess.run(
        ["systemctl", "--user", "status", "takopi.service"],
        capture_output=False,
        check=False,
    )
    raise typer.Exit(code=result.returncode)


def daemon_logs(
    follow: bool = typer.Option(
        False,
        "--follow",
        "-f",
        help="Follow log output.",
    ),
    lines: int = typer.Option(
        50,
        "--lines",
        "-n",
        help="Number of lines to show.",
    ),
) -> None:
    """Show logs from the takopi systemd service."""
    cmd = ["journalctl", "--user", "-u", "takopi.service", f"-n{lines}"]
    if follow:
        cmd.append("-f")
    result = subprocess.run(cmd, check=False)
    raise typer.Exit(code=result.returncode)
