import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_SERVICE_NAME = "recorder.service"
_SERVICE_PATH = Path("/etc/systemd/system") / _SERVICE_NAME

_UNIT_TEMPLATE = """[Unit]
Description=Raspberry Pi continuous video recorder
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


class SystemdService:
    def __init__(self, restart_delay_seconds: int = 10) -> None:
        self._restart_delay_seconds = restart_delay_seconds

    def install(self) -> None:
        self._require_root()
        _SERVICE_PATH.write_text(self._render_unit())
        self._systemctl("daemon-reload")
        self._systemctl("enable", "--now", _SERVICE_NAME)
        logger.info("Installed and started %s", _SERVICE_NAME)

    def uninstall(self) -> None:
        self._require_root()
        self._systemctl("disable", "--now", _SERVICE_NAME)
        _SERVICE_PATH.unlink(missing_ok=True)
        self._systemctl("daemon-reload")
        logger.info("Removed %s", _SERVICE_NAME)

    def _render_unit(self) -> str:
        return _UNIT_TEMPLATE.format(
            exec_start=f"{self._resolve_exec_path()} run",
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
