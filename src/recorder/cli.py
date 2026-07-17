import logging
from pathlib import Path

import click

from .config import RecorderConfig
from .main import run_recorder, run_telegram_bot
from .service import RecorderServiceKind, SystemdService

_DEFAULTS = RecorderConfig()


class VideoSizeParamType(click.ParamType):
    name = "WIDTHxHEIGHT"

    def convert(
        self,
        value: str | tuple[int, int],
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> tuple[int, int]:
        if isinstance(value, tuple):
            return value
        try:
            width_str, height_str = value.lower().split("x")
            return int(width_str), int(height_str)
        except ValueError:
            self.fail(f"{value!r} is not a valid WIDTHxHEIGHT resolution", param, ctx)


VIDEO_SIZE = VideoSizeParamType()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@click.group()
def cli() -> None:
    _configure_logging()


@cli.command()
@click.option(
    "--recordings-dir",
    type=click.Path(path_type=Path),
    default=_DEFAULTS.recordings_dir,
    show_default=True,
    help="Fallback recording directory when no USB drive is present",
)
@click.option(
    "--video-size",
    type=VIDEO_SIZE,
    default=_DEFAULTS.video_size,
    show_default=True,
    help="Recording resolution",
)
@click.option("--framerate", type=int, default=_DEFAULTS.framerate, show_default=True)
@click.option("--bitrate", type=int, default=_DEFAULTS.bitrate, show_default=True)
@click.option(
    "--rotation-hours",
    type=int,
    default=_DEFAULTS.rotation_hours,
    show_default=True,
    help="Hours per recording file before rotating",
)
@click.option(
    "--min-free-space-gb",
    type=float,
    default=_DEFAULTS.min_free_space_gb,
    show_default=True,
)
@click.option(
    "--usb-mount-path",
    type=click.Path(path_type=Path),
    default=_DEFAULTS.usb_mount_path,
    show_default=True,
)
@click.option(
    "--poll-interval-seconds",
    type=int,
    default=_DEFAULTS.poll_interval_seconds,
    show_default=True,
)
@click.option(
    "--hotplug-check-interval-seconds",
    type=int,
    default=_DEFAULTS.hotplug_check_interval_seconds,
    show_default=True,
)
@click.option(
    "--watchdog-restart-delay-seconds",
    type=int,
    default=_DEFAULTS.watchdog_restart_delay_seconds,
    show_default=True,
)
@click.option(
    "--enable-timestamp-overlay/--disable-timestamp-overlay",
    "enable_timestamp_overlay",
    default=_DEFAULTS.enable_timestamp_overlay,
    show_default=True,
    help="Burn the current timestamp into each frame (requires cv2)",
)
def run(
    recordings_dir: Path,
    video_size: tuple[int, int],
    framerate: int,
    bitrate: int,
    rotation_hours: int,
    min_free_space_gb: float,
    usb_mount_path: Path,
    poll_interval_seconds: int,
    hotplug_check_interval_seconds: int,
    watchdog_restart_delay_seconds: int,
    enable_timestamp_overlay: bool,
) -> None:
    config = RecorderConfig(
        recordings_dir=recordings_dir,
        video_size=video_size,
        framerate=framerate,
        bitrate=bitrate,
        rotation_hours=rotation_hours,
        min_free_space_gb=min_free_space_gb,
        usb_mount_path=usb_mount_path,
        poll_interval_seconds=poll_interval_seconds,
        hotplug_check_interval_seconds=hotplug_check_interval_seconds,
        watchdog_restart_delay_seconds=watchdog_restart_delay_seconds,
        enable_timestamp_overlay=enable_timestamp_overlay,
    )
    try:
        run_recorder(config)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command()
@click.option(
    "--recordings-dir",
    type=click.Path(path_type=Path),
    default=_DEFAULTS.recordings_dir,
    show_default=True,
    help="Fallback recording directory when no USB drive is present",
)
@click.option(
    "--usb-mount-path",
    type=click.Path(path_type=Path),
    default=_DEFAULTS.usb_mount_path,
    show_default=True,
)
@click.option(
    "--min-free-space-gb",
    type=float,
    default=_DEFAULTS.min_free_space_gb,
    show_default=True,
)
def telegram(
    recordings_dir: Path, usb_mount_path: Path, min_free_space_gb: float
) -> None:
    config = RecorderConfig(
        recordings_dir=recordings_dir,
        usb_mount_path=usb_mount_path,
        min_free_space_gb=min_free_space_gb,
    )
    try:
        run_telegram_bot(config)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command("install-service")
@click.option(
    "--restart-delay-seconds",
    type=int,
    default=_DEFAULTS.watchdog_restart_delay_seconds,
    show_default=True,
    help="Delay before systemd restarts the recorder after a crash or reboot",
)
def install_service(restart_delay_seconds: int) -> None:
    try:
        for kind in RecorderServiceKind:
            SystemdService(kind, restart_delay_seconds).install()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        "Reminder: /restart in Telegram needs a sudoers rule for "
        "`systemctl restart recorder.service` — see the plan/README for the "
        "one-line visudo setup."
    )


@cli.command("uninstall-service")
def uninstall_service() -> None:
    try:
        for kind in RecorderServiceKind:
            SystemdService(kind).uninstall()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
