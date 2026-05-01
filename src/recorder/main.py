import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput

_STATE_FILE = (
    Path(__file__).resolve().parent.parent.parent / ".tmp" / "recorder_state.json"
)


@dataclass(kw_only=True, slots=True, frozen=True)
class RecorderConfig:
    recordings_dir: Path = Path.home() / "Desktop" / "Recordings"
    video_size: tuple[int, int] = (1920, 1080)
    framerate: int = 24
    bitrate: int = 1_000_000
    rotation_hours: int = 1
    min_free_space_gb: float = 2.0
    usb_mount_path: Path = Path("/media")


@dataclass(kw_only=True, slots=True)
class SelectorState:
    current_target: str


def _load_selector_state() -> SelectorState:
    try:
        raw = json.loads(_STATE_FILE.read_text())
        return SelectorState(current_target=raw.get("current_target", ""))
    except (OSError, json.JSONDecodeError):
        return SelectorState(current_target="")


def _save_selector_state(state: SelectorState) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps({"current_target": state.current_target}))
    except OSError:
        pass


def is_writable(path: Path) -> bool:
    try:
        return os.access(path, os.W_OK)
    except OSError:
        return False


def get_usb_devices(config: RecorderConfig) -> list[Path]:
    usb_devices = []
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
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            continue

    return oldest_file


class DriveSelector:
    def __init__(self, config: RecorderConfig) -> None:
        self._config = config
        self._current_target_index = 0
        self._restore_index_from_state()

    def _restore_index_from_state(self) -> None:
        state = _load_selector_state()
        if not state.current_target:
            return

        saved_path = Path(state.current_target)
        usb_devices = get_usb_devices(self._config)
        desktop_target = self._config.recordings_dir.parent
        targets = usb_devices if usb_devices else [desktop_target]

        for i, target in enumerate(targets):
            if target == saved_path:
                self._current_target_index = i
                return

    def get_next_recording_dir(self) -> Path:
        config = self._config
        usb_devices = get_usb_devices(config)
        desktop_dir = config.recordings_dir
        desktop_target = desktop_dir.parent
        min_space = config.min_free_space_gb

        targets = usb_devices if usb_devices else [desktop_target]
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

        _save_selector_state(SelectorState(current_target=str(current_target)))

        if current_target == desktop_target:
            return desktop_dir
        return current_target / "Recordings"


def create_filename(drive_selector: DriveSelector) -> Path:
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    recordings_dir = drive_selector.get_next_recording_dir()
    daily_folder = recordings_dir / date_str

    try:
        daily_folder.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise RuntimeError(
            f"Permission denied: Cannot create directory {daily_folder}."
        ) from e
    except OSError as e:
        raise RuntimeError(f"Cannot create directory {daily_folder}: {e}") from e

    return daily_folder / f"recording_{timestamp}.h264"


def main():
    config = RecorderConfig()
    config.recordings_dir.mkdir(parents=True, exist_ok=True)
    drive_selector = DriveSelector(config)

    picam2 = Picamera2()
    frame_duration = int(1_000_000 / config.framerate)

    video_config = picam2.create_video_configuration(
        main={"size": config.video_size},
        controls={
            "FrameDurationLimits": (frame_duration, frame_duration),
            "NoiseReductionMode": 2,
        },
    )

    picam2.configure(video_config)
    picam2.start()

    try:
        while True:
            filename = create_filename(drive_selector)
            output = FileOutput(str(filename))
            target_drive = filename.parent.parent

            encoder = H264Encoder(bitrate=config.bitrate)
            picam2.start_encoder(encoder, output)
            print(f"Starting recording: {filename}")

            start_time = time.time()
            recording_duration = config.rotation_hours * 3600

            while time.time() - start_time < recording_duration:
                if is_device_full(target_drive, config.min_free_space_gb):
                    print("Drive reached capacity limit. Rotating early.")
                    break
                time.sleep(5)

            picam2.stop_encoder()
            print(f"Recording stopped: {filename}")

    except KeyboardInterrupt:
        print("Recording stopped by user")
    except RuntimeError as e:
        print(f"Error: {e}")
    finally:
        try:
            picam2.stop_encoder()
        except Exception:
            pass
        try:
            picam2.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
