from __future__ import annotations

import logging
import threading
import time
from enum import Enum, auto
from typing import TYPE_CHECKING

from .config import RecorderConfig
from .storage import (
    DriveSelector,
    create_filename,
    finalize_partial,
    get_usb_devices,
    is_device_full,
    log_targets_free_space,
)

if TYPE_CHECKING:
    from picamera2 import Picamera2
    from picamera2.request import CompletedRequest  # ty: ignore[unresolved-import]

logger = logging.getLogger(__name__)

_MISSING_PICAMERA2 = (
    "picamera2 is not installed; the recorder must run on a Raspberry Pi "
    "with libcamera support."
)


class RotationOutcome(Enum):
    TIME_ELAPSED = auto()
    DRIVE_FULL = auto()
    DRIVE_REMOVED = auto()
    USB_INSERTED = auto()
    SHUTDOWN = auto()


_ROTATION_LOG_MESSAGES: dict[RotationOutcome, str] = {
    RotationOutcome.TIME_ELAPSED: "Rotation window elapsed for %s",
    RotationOutcome.DRIVE_FULL: "Drive %s reached capacity; rotating early",
    RotationOutcome.DRIVE_REMOVED: "Target %s no longer present; rotating early",
    RotationOutcome.USB_INSERTED: (
        "USB drive inserted while on fallback %s; rotating to it"
    ),
    RotationOutcome.SHUTDOWN: (
        "Shutdown requested while recording on %s; ending current rotation"
    ),
}


def cv2_available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def _apply_timestamp_overlay(request: CompletedRequest) -> None:
    import cv2
    from picamera2 import MappedArray

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


class Recorder:
    def __init__(
        self,
        config: RecorderConfig,
        drive_selector: DriveSelector,
        shutdown_event: threading.Event,
    ) -> None:
        self._config = config
        self._drive_selector = drive_selector
        self._shutdown_event = shutdown_event
        self._picam2: Picamera2 | None = None

    def run(self) -> None:
        self._picam2 = self._build_picam()
        try:
            while not self._shutdown_event.is_set():
                self._record_one_rotation()
        finally:
            self._close()

    def _build_picam(self) -> Picamera2:
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise RuntimeError(_MISSING_PICAMERA2) from exc

        config = self._config
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
        if config.enable_timestamp_overlay and cv2_available():
            picam2.pre_callback = _apply_timestamp_overlay
        picam2.start()
        return picam2

    def _record_one_rotation(self) -> RotationOutcome:
        assert self._picam2 is not None
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import FileOutput

        config = self._config

        final_path, target_drive = create_filename(self._drive_selector)
        partial_path = final_path.with_name(final_path.name + ".partial")

        log_targets_free_space(config)

        output = FileOutput(str(partial_path))
        encoder = H264Encoder(bitrate=config.bitrate)
        self._picam2.start_encoder(encoder, output)
        logger.info("Starting recording: %s", partial_path)

        outcome = RotationOutcome.TIME_ELAPSED
        try:
            start_time = time.time()
            recording_duration = config.rotation_hours * 3600
            last_hotplug_check = start_time
            was_on_fallback = target_drive == config.recordings_dir.parent

            while time.time() - start_time < recording_duration:
                if self._shutdown_event.wait(config.poll_interval_seconds):
                    outcome = RotationOutcome.SHUTDOWN
                    break

                if is_device_full(target_drive, config.min_free_space_gb):
                    outcome = RotationOutcome.DRIVE_FULL
                    break

                now = time.time()
                if now - last_hotplug_check >= config.hotplug_check_interval_seconds:
                    last_hotplug_check = now
                    if not target_drive.exists():
                        outcome = RotationOutcome.DRIVE_REMOVED
                        break
                    if was_on_fallback and get_usb_devices(config):
                        outcome = RotationOutcome.USB_INSERTED
                        break
        finally:
            try:
                self._picam2.stop_encoder()
            except RuntimeError as exc:
                logger.debug("stop_encoder during rotation end: %s", exc)
            finalize_partial(partial_path, final_path)
            logger.info("Recording finalized: %s", final_path)

        logger.info(_ROTATION_LOG_MESSAGES[outcome], target_drive)
        return outcome

    def _close(self) -> None:
        assert self._picam2 is not None
        try:
            self._picam2.stop_encoder()
        except RuntimeError as exc:
            logger.debug("stop_encoder during shutdown: %s", exc)
        try:
            self._picam2.stop()
        except RuntimeError as exc:
            logger.debug("stop during shutdown: %s", exc)
        try:
            self._picam2.close()
        except RuntimeError as exc:
            logger.debug("close during shutdown: %s", exc)
