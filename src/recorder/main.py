import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput

CONFIG = {
    "recordings_dir": Path.home() / "Desktop" / "Recordings",
    "video_size": (1920, 1080),
    "framerate": 24,
    "bitrate": 1000000,
    "rotation_hours": 1,
    "min_free_space_gb": 2,
    "usb_mount_path": Path("/media"),
}

_current_usb_index = 0


def is_writable(path: Path) -> bool:
    try:
        return os.access(path, os.W_OK)
    except OSError:
        return False


def get_usb_devices() -> list[Path]:
    usb_devices = []
    if not CONFIG["usb_mount_path"].exists():
        return usb_devices

    try:
        for user_dir in CONFIG["usb_mount_path"].iterdir():
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
    stat = shutil.disk_usage(path)
    return stat.free / (1024**3)


def is_device_full(path: Path, min_space_gb: float) -> bool:
    return get_available_space_gb(path) < min_space_gb


def get_all_recording_dirs() -> list[Path]:
    recording_dirs = []
    usb_devices = get_usb_devices()
    for usb_device in usb_devices:
        recording_dirs.append(usb_device / "Recordings")
    recording_dirs.append(CONFIG["recordings_dir"])
    return recording_dirs


def find_oldest_recording() -> Path | None:
    oldest_file: Path | None = None
    oldest_time: float | None = None

    for recording_dir in get_all_recording_dirs():
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


def free_up_space(target_path: Path, min_space_gb: float) -> None:
    while is_device_full(target_path, min_space_gb):
        oldest_file = find_oldest_recording()
        if oldest_file is None:
            raise RuntimeError(
                f"No recordings found to delete and insufficient storage: "
                f"need {min_space_gb}GB free on {target_path}"
            )

        try:
            file_size_gb = oldest_file.stat().st_size / (1024**3)
            oldest_file.unlink()
            print(f"Deleted oldest recording: {oldest_file} ({file_size_gb:.2f}GB)")
        except (OSError, PermissionError) as e:
            raise RuntimeError(
                f"Failed to delete oldest recording {oldest_file}: {e}"
            ) from e


def get_next_recording_dir() -> Path:
    global _current_usb_index
    usb_devices = get_usb_devices()
    desktop_dir = CONFIG["recordings_dir"]
    min_space = CONFIG["min_free_space_gb"]

    if usb_devices:
        for _ in range(len(usb_devices)):
            usb_device = usb_devices[_current_usb_index % len(usb_devices)]
            if is_device_full(usb_device, min_space):
                free_up_space(usb_device, min_space)
            _current_usb_index += 1
            return usb_device / "Recordings"

    if is_device_full(desktop_dir.parent, min_space):
        free_up_space(desktop_dir.parent, min_space)

    return desktop_dir


def create_filename() -> Path:
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    recordings_dir = get_next_recording_dir()
    daily_folder = recordings_dir / date_str

    try:
        daily_folder.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise RuntimeError(
            f"Permission denied: Cannot create directory {daily_folder}. "
            f"Please check write permissions on the USB device."
        ) from e
    except OSError as e:
        raise RuntimeError(f"Cannot create directory {daily_folder}: {e}") from e

    return daily_folder / f"recording_{timestamp}.h264"


def main():
    CONFIG["recordings_dir"].mkdir(parents=True, exist_ok=True)

    picam2 = Picamera2()
    frame_duration = int(1_000_000 / CONFIG["framerate"])

    video_config = picam2.create_video_configuration(
        main={"size": CONFIG["video_size"]},
        controls={
            "FrameDurationLimits": (frame_duration, frame_duration),
            "NoiseReductionMode": 2,
        },
    )

    picam2.configure(video_config)
    encoder = H264Encoder(bitrate=CONFIG["bitrate"])
    picam2.start()

    try:
        while True:
            filename = create_filename()
            output = FileOutput(str(filename))

            available_space = get_available_space_gb(filename.parent.parent)
            print(
                f"Starting recording: {filename} "
                f"(Available space: {available_space:.2f}GB)"
            )
            picam2.start_encoder(encoder, output)

            time.sleep(CONFIG["rotation_hours"] * 3600)

            picam2.stop_encoder()
            print(f"Recording stopped: {filename}")

    except KeyboardInterrupt:
        print("Recording stopped by user")
    except RuntimeError as e:
        print(f"Error: {e}")
    finally:
        picam2.stop_encoder()
        picam2.stop()


if __name__ == "__main__":
    main()
