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


def get_usb_devices() -> list[Path]:
    usb_devices = []
    if CONFIG["usb_mount_path"].exists():
        for item in CONFIG["usb_mount_path"].iterdir():
            if item.is_dir() and not item.name.startswith("."):
                usb_devices.append(item)
    return sorted(usb_devices)


def get_available_space_gb(path: Path) -> float:
    stat = shutil.disk_usage(path)
    return stat.free / (1024**3)


def is_device_full(path: Path, min_space_gb: float) -> bool:
    return get_available_space_gb(path) < min_space_gb


def get_next_recording_dir() -> Path:
    global _current_usb_index
    usb_devices = get_usb_devices()
    desktop_dir = CONFIG["recordings_dir"]
    min_space = CONFIG["min_free_space_gb"]

    if usb_devices:
        for _ in range(len(usb_devices)):
            usb_device = usb_devices[_current_usb_index % len(usb_devices)]
            if not is_device_full(usb_device, min_space):
                _current_usb_index += 1
                return usb_device / "Recordings"
            _current_usb_index += 1

    if not is_device_full(desktop_dir.parent, min_space):
        return desktop_dir

    raise RuntimeError(
        f"No available storage: all USB devices and Desktop have less than "
        f"{min_space}GB free"
    )


def create_filename() -> Path:
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    recordings_dir = get_next_recording_dir()
    daily_folder = recordings_dir / date_str
    daily_folder.mkdir(parents=True, exist_ok=True)

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
