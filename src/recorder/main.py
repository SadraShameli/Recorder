import logging
import signal
import threading
from types import FrameType

from .camera import _MISSING_PICAMERA2, Recorder, cv2_available
from .config import RecorderConfig
from .storage import DriveSelector, get_targets, is_device_full, is_writable

logger = logging.getLogger(__name__)


def preflight_checks(config: RecorderConfig) -> None:
    try:
        from picamera2 import Picamera2
    except ImportError as exc:
        raise RuntimeError(_MISSING_PICAMERA2) from exc

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

    if config.enable_timestamp_overlay and not cv2_available():
        logger.warning(
            "Timestamp overlay enabled but cv2 not available; skipping overlay"
        )


def _install_signal_handlers(shutdown_event: threading.Event) -> None:
    def _handler(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s; initiating graceful shutdown", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def run_recorder(config: RecorderConfig) -> None:
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
            Recorder(config, drive_selector, shutdown_event).run()
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
