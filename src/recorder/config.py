from dataclasses import dataclass
from pathlib import Path


@dataclass(kw_only=True, slots=True, frozen=True)
class RecorderConfig:
    recordings_dir: Path = Path.home() / "Desktop" / "Recordings"
    video_size: tuple[int, int] = (1920, 1080)
    framerate: int = 24
    bitrate: int = 1_000_000
    rotation_hours: int = 1
    min_free_space_gb: float = 1.0
    usb_mount_path: Path = Path("/media")
    poll_interval_seconds: int = 5
    hotplug_check_interval_seconds: int = 30
    watchdog_restart_delay_seconds: int = 10
    enable_timestamp_overlay: bool = True
