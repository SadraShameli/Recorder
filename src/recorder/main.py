import json
import logging
import os
import shutil
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import FrameType

from picamera2 import MappedArray, Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput

try:
    import cv2  # ty: ignore[unresolved-import]

    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False


_STATE_FILE = (
    Path(__file__).resolve().parent.parent.parent / ".tmp" / "recorder_state.json"
)

logger = logging.getLogger("recorder")


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


@dataclass(kw_only=True, slots=True)
class SelectorState:
    current_target: str


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

    def _load_state(self) -> SelectorState:
        try:
            raw = json.loads(_STATE_FILE.read_text())
            return SelectorState(current_target=raw.get("current_target", ""))
        except (OSError, json.JSONDecodeError):
            return SelectorState(current_target="")

    def _save_state(self, state: SelectorState) -> None:
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({"current_target": state.current_target}))
        except OSError as exc:
            logger.warning("Failed to persist selector state: %s", exc)

    def _restore_index_from_state(self) -> None:
        state = self._load_state()
        if not state.current_target:
            return

        saved_path = Path(state.current_target)
        targets = get_targets(self._config)

        for i, target in enumerate(targets):
            if target == saved_path:
                self._current_target_index = i
                return

    def get_next_recording_dir(self) -> Path:
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

        self._save_state(SelectorState(current_target=str(current_target)))

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


def _apply_timestamp_overlay(request) -> None:
    timestamp_text = time.strftime("%Y-%m-%d %H:%M:%S")
    with MappedArray(request, "main") as mapped:
        cv2.putText(
            mapped.array,
            timestamp_text,
            (10, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )


def finalize_partial(partial: Path, final: Path) -> None:
    if not partial.exists():
        return
    try:
        partial.rename(final)
    except OSError as exc:
        logger.error("Failed to finalize %s -> %s: %s", partial, final, exc)


def preflight_checks(config: RecorderConfig) -> None:
    cameras = Picamera2.global_camera_info()
    if not cameras:
        raise RuntimeError("No camera detected by Picamera2")

    config.recordings_dir.mkdir(parents=True, exist_ok=True)
    if not is_writable(config.recordings_dir):
        raise RuntimeError(f"Recordings dir not writable: {config.recordings_dir}")

    targets = get_targets(config)
    if not any(
        not is_device_full(target, config.min_free_space_gb) for target in targets
    ):
        logger.warning(
            "All targets below %.2f GB free; rotation will rely on auto-cleanup",
            config.min_free_space_gb,
        )

    if config.enable_timestamp_overlay and not _HAS_CV2:
        logger.warning(
            "Timestamp overlay enabled but cv2 not available; skipping overlay"
        )


def _build_picam(config: RecorderConfig) -> Picamera2:
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
    if config.enable_timestamp_overlay and _HAS_CV2:
        picam2.pre_callback = _apply_timestamp_overlay
    picam2.start()
    return picam2


def _record_one_rotation(
    picam2: Picamera2,
    config: RecorderConfig,
    drive_selector: DriveSelector,
    shutdown_event: threading.Event,
) -> None:
    final_path = create_filename(drive_selector)
    partial_path = final_path.with_name(final_path.name + ".partial")
    target_drive = final_path.parent.parent

    log_targets_free_space(config)

    output = FileOutput(str(partial_path))
    encoder = H264Encoder(bitrate=config.bitrate)
    picam2.start_encoder(encoder, output)
    logger.info("Starting recording: %s", partial_path)

    try:
        start_time = time.time()
        recording_duration = config.rotation_hours * 3600
        last_hotplug_check = start_time
        was_on_fallback = target_drive == config.recordings_dir.parent

        while time.time() - start_time < recording_duration:
            if shutdown_event.wait(config.poll_interval_seconds):
                logger.info("Shutdown requested; ending current rotation")
                return

            if is_device_full(target_drive, config.min_free_space_gb):
                logger.info("Drive %s reached capacity; rotating early", target_drive)
                return

            now = time.time()
            if now - last_hotplug_check >= config.hotplug_check_interval_seconds:
                last_hotplug_check = now
                if not target_drive.exists():
                    logger.warning(
                        "Target %s no longer present; rotating early", target_drive
                    )
                    return
                if was_on_fallback and get_usb_devices(config):
                    logger.info("USB drive inserted while on fallback; rotating to it")
                    return
    finally:
        try:
            picam2.stop_encoder()
        except RuntimeError as exc:
            logger.debug("stop_encoder during rotation end: %s", exc)
        finalize_partial(partial_path, final_path)
        logger.info("Recording finalized: %s", final_path)


def _run_recorder(
    config: RecorderConfig,
    drive_selector: DriveSelector,
    shutdown_event: threading.Event,
) -> None:
    picam2 = _build_picam(config)
    try:
        while not shutdown_event.is_set():
            _record_one_rotation(picam2, config, drive_selector, shutdown_event)
    finally:
        try:
            picam2.stop_encoder()
        except RuntimeError as exc:
            logger.debug("stop_encoder during shutdown: %s", exc)
        try:
            picam2.stop()
        except RuntimeError as exc:
            logger.debug("stop during shutdown: %s", exc)
        try:
            picam2.close()
        except RuntimeError as exc:
            logger.debug("close during shutdown: %s", exc)


def _install_signal_handlers(shutdown_event: threading.Event) -> None:
    def _handler(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s; initiating graceful shutdown", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    _configure_logging()
    config = RecorderConfig()

    shutdown_event = threading.Event()
    _install_signal_handlers(shutdown_event)

    try:
        preflight_checks(config)
    except RuntimeError as exc:
        logger.error("Pre-flight failed: %s", exc)
        raise

    drive_selector = DriveSelector(config)

    while not shutdown_event.is_set():
        try:
            _run_recorder(config, drive_selector, shutdown_event)
        except Exception:
            if shutdown_event.is_set():
                break
            logger.exception(
                "Recorder session crashed; restarting in %ds",
                config.watchdog_restart_delay_seconds,
            )
            if shutdown_event.wait(config.watchdog_restart_delay_seconds):
                break

    logger.info("Recorder shutdown complete")


if __name__ == "__main__":
    main()
