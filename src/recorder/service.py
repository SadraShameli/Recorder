import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

_SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")

_UNIT_TEMPLATE = """[Unit]
Description={description}
After=multi-user.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec={restart_delay}
User={user}

[Install]
WantedBy=multi-user.target
"""


@dataclass(kw_only=True, slots=True, frozen=True)
class _ServiceSpec:
    service_name: str
    exec_subcommand: str
    description: str


class RecorderServiceKind(Enum):
    RECORDER = _ServiceSpec(
        service_name="recorder.service",
        exec_subcommand="run",
        description="Raspberry Pi continuous video recorder",
    )
    TELEGRAM = _ServiceSpec(
        service_name="recorder-telegram.service",
        exec_subcommand="telegram",
        description="Raspberry Pi recorder Telegram bot",
    )

    @property
    def service_name(self) -> str:
        return self.value.service_name

    @property
    def exec_subcommand(self) -> str:
        return self.value.exec_subcommand

    @property
    def description(self) -> str:
        return self.value.description


class SystemdService:
    def __init__(
        self, kind: RecorderServiceKind, restart_delay_seconds: int = 10
    ) -> None:
        self._kind = kind
        self._restart_delay_seconds = restart_delay_seconds
        self._service_path = _SYSTEMD_UNIT_DIR / kind.service_name

    def install(self) -> None:
        self._require_root()
        self._service_path.write_text(self._render_unit())
        self._systemctl("daemon-reload")
        self._systemctl("enable", "--now", self._kind.service_name)
        logger.info("Installed and started %s", self._kind.service_name)

    def uninstall(self) -> None:
        self._require_root()
        if not self._service_path.exists():
            logger.info("%s is not installed; nothing to do", self._kind.service_name)
            return
        self._systemctl("disable", "--now", self._kind.service_name)
        self._service_path.unlink(missing_ok=True)
        self._systemctl("daemon-reload")
        logger.info("Removed %s", self._kind.service_name)

    def is_active(self) -> bool:
        result = subprocess.run(
            ["systemctl", "is-active", self._kind.service_name],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == "active"

    def restart(self) -> None:
        try:
            subprocess.run(
                ["sudo", "systemctl", "restart", self._kind.service_name],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "sudo/systemctl not found; this requires systemd on Linux"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Failed to restart {self._kind.service_name}: "
                f"{exc.stderr.strip()}. Check that the sudoers rule for this "
                "command is configured."
            ) from exc

    def active_since_seconds(self) -> float | None:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                self._kind.service_name,
                "--property=ActiveEnterTimestampMonotonic",
                "--value",
            ],
            capture_output=True,
            text=True,
        )
        raw = result.stdout.strip()
        if not raw or raw == "0":
            return None
        return time.monotonic() - (int(raw) / 1_000_000)

    def logs(self, lines: int) -> str:
        result = subprocess.run(
            [
                "journalctl",
                "-u",
                self._kind.service_name,
                "-n",
                str(lines),
                "--no-pager",
            ],
            capture_output=True,
            text=True,
        )
        return result.stdout

    def _render_unit(self) -> str:
        return _UNIT_TEMPLATE.format(
            description=self._kind.description,
            exec_start=f"{self._resolve_exec_path()} {self._kind.exec_subcommand}",
            restart_delay=self._restart_delay_seconds,
            user=os.environ.get("SUDO_USER") or os.environ.get("USER", "root"),
        )

    def _resolve_exec_path(self) -> Path:
        exe = Path(sys.argv[0]).resolve()
        if not exe.is_file():
            raise RuntimeError(f"Could not resolve the recorder executable ({exe})")
        return exe

    def _require_root(self) -> None:
        if os.geteuid() != 0:
            raise RuntimeError("This action requires root; re-run with sudo")

    def _systemctl(self, *args: str) -> None:
        try:
            subprocess.run(["systemctl", *args], check=True)
        except FileNotFoundError as exc:
            raise RuntimeError(
                "systemctl not found; this requires systemd on Linux"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"systemctl {' '.join(args)} failed: {exc}") from exc
