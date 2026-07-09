import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .config import RecorderConfig

_STATE_FILE = (
    Path(__file__).resolve().parent.parent.parent / ".tmp" / "recorder_state.json"
)

logger = logging.getLogger(__name__)


class _SelectorState(BaseModel):
    current_target: str = ""


def is_writable(path: Path) -> bool:
    try:
        return os.access(path, os.W_OK)
    except OSError:
        return False


def get_usb_devices(config: RecorderConfig) -> list[Path]:
    usb_devices: list[Path] = []
    if not config.usb_mount_path.exists():
        return usb_devices

    try:
        for user_dir in config.usb_mount_path.iterdir():
            if not user_dir.is_dir() or user_dir.name.startswith("."):
                continue

            try:
                for device_dir in user_dir.iterdir():
                    if device_dir.is_dir() and not device_dir.name.startswith("."):
                        if is_writable(device_dir):
                            usb_devices.append(device_dir)
            except PermissionError:
                continue
    except PermissionError:
        pass

    return sorted(usb_devices)


def get_available_space_gb(path: Path) -> float:
    try:
        stat = shutil.disk_usage(path)
        return stat.free / (1024**3)
    except OSError:
        return 0.0


def is_device_full(path: Path, min_space_gb: float) -> bool:
    return get_available_space_gb(path) < min_space_gb


def get_targets(config: RecorderConfig) -> list[Path]:
    usb_devices = get_usb_devices(config)
    return usb_devices if usb_devices else [config.recordings_dir.parent]


def log_targets_free_space(config: RecorderConfig) -> None:
    for target in get_targets(config):
        logger.info("Target %s: %.2f GB free", target, get_available_space_gb(target))


def find_oldest_recording(
    targets: list[Path], desktop_dir: Path, desktop_target: Path
) -> Path | None:
    oldest_file: Path | None = None
    oldest_time: float | None = None

    for target in targets:
        recording_dir = (
            desktop_dir if target == desktop_target else target / "Recordings"
        )

        if not recording_dir.exists():
            continue

        try:
            for date_folder in recording_dir.iterdir():
                if not date_folder.is_dir():
                    continue

                try:
                    for file_path in date_folder.iterdir():
                        if file_path.is_file() and file_path.suffix == ".h264":
                            file_time = file_path.stat().st_mtime
                            if oldest_time is None or file_time < oldest_time:
                                oldest_time = file_time
                                oldest_file = file_path
                except PermissionError, OSError:
                    continue
        except PermissionError, OSError:
            continue

    return oldest_file


class DriveSelector:
    def __init__(self, config: RecorderConfig) -> None:
        self._config = config
        self._current_target_index = 0
        self._restore_index_from_state()

    def _load_state(self) -> str:
        try:
            raw = _STATE_FILE.read_text()
        except OSError:
            return ""

        try:
            state = _SelectorState.model_validate_json(raw)
        except ValidationError:
            return ""

        return state.current_target

    def _save_state(self, current_target: str) -> None:
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            state = _SelectorState(current_target=current_target)
            _STATE_FILE.write_text(state.model_dump_json())
        except OSError as exc:
            logger.warning("Failed to persist selector state: %s", exc)

    def _restore_index_from_state(self) -> None:
        saved_target = self._load_state()
        if not saved_target:
            return

        saved_path = Path(saved_target)
        targets = get_targets(self._config)

        for i, target in enumerate(targets):
            if target == saved_path:
                self._current_target_index = i
                return

    def get_next_recording_dir(self) -> tuple[Path, Path]:
        config = self._config
        desktop_dir = config.recordings_dir
        desktop_target = desktop_dir.parent
        min_space = config.min_free_space_gb

        targets = get_targets(config)
        self._current_target_index = self._current_target_index % len(targets)

        for offset in range(len(targets)):
            candidate = (self._current_target_index + offset) % len(targets)
            if not is_device_full(targets[candidate], min_space):
                self._current_target_index = candidate
                break

        current_target = targets[self._current_target_index]

        while is_device_full(current_target, min_space):
            oldest_file = find_oldest_recording(
                [current_target], desktop_dir, desktop_target
            )
            if oldest_file is None:
                raise RuntimeError(
                    "No recordings found to delete and insufficient storage."
                )

            try:
                oldest_file.unlink()
                parent_dir = oldest_file.parent
                if not any(parent_dir.iterdir()):
                    parent_dir.rmdir()
            except (OSError, PermissionError) as e:
                raise RuntimeError(
                    f"Failed to delete oldest recording {oldest_file}: {e}"
                ) from e

        self._save_state(str(current_target))

        if current_target == desktop_target:
            return desktop_dir, current_target
        return current_target / "Recordings", current_target


def create_filename(drive_selector: DriveSelector) -> tuple[Path, Path]:
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    recordings_dir, drive_root = drive_selector.get_next_recording_dir()
    daily_folder = recordings_dir / date_str

    try:
        daily_folder.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise RuntimeError(
            f"Permission denied: Cannot create directory {daily_folder}."
        ) from e
    except OSError as e:
        raise RuntimeError(f"Cannot create directory {daily_folder}: {e}") from e

    return daily_folder / f"recording_{timestamp}.h264", drive_root


def finalize_partial(partial: Path, final: Path) -> None:
    if not partial.exists():
        return
    try:
        partial.rename(final)
    except OSError as exc:
        logger.error("Failed to finalize %s -> %s: %s", partial, final, exc)
