import logging
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx

from .. import paths, storage
from ..config import RecorderConfig
from ..service import RecorderServiceKind, SystemdService
from .client import (
    InlineButton,
    InlineKeyboard,
    TelegramCallbackQuery,
    TelegramClient,
    TelegramMessage,
    TelegramUpdate,
)
from .commands import TelegramCallbackAction, TelegramCommand
from .config import TelegramBotConfig, get_telegram_bot_config, save_telegram_bot_config
from .env import Env

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_SECONDS = 10
_ERROR_BACKOFF_SECONDS = 5
_LIST_PAGE_SIZE = 5
_MAX_MESSAGE_CHARS = 4000
_LOG_LINES = 100
_LIVE_SNAPSHOT_TIMEOUT_SECONDS = 15
_LIVE_SNAPSHOT_POLL_INTERVAL_SECONDS = 0.5

_COMMAND_LIST_TEXT = (
    "/status - recorder service health and active drive\n"
    "/drives - storage targets, free space, recording counts\n"
    "/preview - latest saved camera snapshot\n"
    "/live - capture and send a fresh snapshot right now\n"
    "/autopush - toggle automatic preview push\n"
    "/list - browse and delete recordings\n"
    "/clear - bulk-delete recordings\n"
    "/restart - restart the recorder service\n"
    "/logs [recorder|telegram] - recent service logs\n"
    "/uptime - system and service uptime"
)


class TelegramBot:
    def __init__(self, config: RecorderConfig, shutdown_event: threading.Event) -> None:
        self._config = config
        self._shutdown_event = shutdown_event
        self._client = TelegramClient(Env.telegram_bot_token())
        self._chat_ids = Env.telegram_bot_chat_ids()
        self._bot_config: TelegramBotConfig = get_telegram_bot_config()
        self._pending_deletes: dict[str, Path] = {}
        self._last_pushed_preview = self._find_latest_preview()

        self._commands = {
            TelegramCommand.START: self._handle_start,
            TelegramCommand.HELP: self._handle_help,
            TelegramCommand.STATUS: self._handle_status,
            TelegramCommand.DRIVES: self._handle_drives,
            TelegramCommand.PREVIEW: self._handle_preview,
            TelegramCommand.LIVE: self._handle_live,
            TelegramCommand.AUTOPUSH: self._handle_autopush,
            TelegramCommand.LIST: self._handle_list,
            TelegramCommand.CLEAR: self._handle_clear,
            TelegramCommand.RESTART: self._handle_restart,
            TelegramCommand.LOGS: self._handle_logs,
            TelegramCommand.UPTIME: self._handle_uptime,
        }
        self._callbacks = {
            TelegramCallbackAction.LIST_PAGE: self._on_list_page,
            TelegramCallbackAction.DELETE_REQUEST: self._on_delete_request,
            TelegramCallbackAction.DELETE_CONFIRM: self._on_delete_confirm,
            TelegramCallbackAction.DELETE_CANCEL: self._on_delete_cancel,
            TelegramCallbackAction.CLEAR_SELECT: self._on_clear_select,
            TelegramCallbackAction.CLEAR_CONFIRM: self._on_clear_confirm,
            TelegramCallbackAction.CLEAR_CANCEL: self._on_clear_cancel,
            TelegramCallbackAction.AUTOPUSH_ON: self._on_autopush_on,
            TelegramCallbackAction.AUTOPUSH_OFF: self._on_autopush_off,
            TelegramCallbackAction.RESTART_CONFIRM: self._on_restart_confirm,
            TelegramCallbackAction.RESTART_CANCEL: self._on_restart_cancel,
        }
        self._command_by_value = {command.value: command for command in TelegramCommand}
        self._callback_by_value = {
            action.value: action for action in TelegramCallbackAction
        }

    def run(self) -> None:
        offset = 0
        try:
            while not self._shutdown_event.is_set():
                offset = self._poll_once(offset)
        finally:
            self._client.close()

    def _poll_once(self, offset: int) -> int:
        try:
            updates = self._client.get_updates(
                offset, timeout_seconds=_POLL_TIMEOUT_SECONDS
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("Telegram API rejected request, retrying: %s", exc)
            self._shutdown_event.wait(_ERROR_BACKOFF_SECONDS)
            return offset
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.debug("Telegram poll network error, retrying: %s", exc)
            self._shutdown_event.wait(_ERROR_BACKOFF_SECONDS)
            return offset

        for update in updates:
            offset = update.update_id + 1
            try:
                self._dispatch(update)
            except Exception:
                logger.exception("Failed handling update %s", update.update_id)

        self._maybe_push_preview()
        return offset

    def _dispatch(self, update: TelegramUpdate) -> None:
        if update.message is not None:
            self._dispatch_message(update.message)
        elif update.callback_query is not None:
            self._dispatch_callback(update.callback_query)

    def _dispatch_message(self, message: TelegramMessage) -> None:
        if message.chat_id not in self._chat_ids or not message.text:
            return

        parts = message.text.strip().split()
        command = self._command_by_value.get(parts[0].split("@")[0])
        if command is None:
            return

        self._commands[command](message.chat_id, parts[1:])

    def _dispatch_callback(self, callback: TelegramCallbackQuery) -> None:
        if callback.chat_id not in self._chat_ids:
            self._client.answer_callback_query(callback.callback_id)
            return

        action_token, _, payload = callback.data.partition(":")
        action = self._callback_by_value.get(action_token)
        if action is None:
            self._client.answer_callback_query(callback.callback_id, "Unknown action")
            return

        self._callbacks[action](callback, payload)

    def _desktop_dirs(self) -> tuple[Path, Path]:
        desktop_dir = self._config.recordings_dir
        return desktop_dir, desktop_dir.parent

    def _find_latest_preview(self) -> Path | None:
        desktop_dir, desktop_target = self._desktop_dirs()
        targets = storage.get_targets(self._config)
        return storage.find_newest_preview(targets, desktop_dir, desktop_target)

    def _maybe_push_preview(self) -> None:
        if not self._bot_config.auto_push_preview:
            return

        preview = self._find_latest_preview()
        if preview is None or preview == self._last_pushed_preview:
            return

        self._last_pushed_preview = preview
        for chat_id in self._chat_ids:
            try:
                self._client.send_photo(chat_id, preview, caption=preview.stem)
            except (
                httpx.TimeoutException,
                httpx.TransportError,
                httpx.HTTPStatusError,
            ) as exc:
                logger.warning("Failed to auto-push preview to %s: %s", chat_id, exc)

    def _handle_start(self, chat_id: str, args: list[str]) -> None:
        self._client.send_message(chat_id, f"Recorder bot\n\n{_COMMAND_LIST_TEXT}")

    def _handle_help(self, chat_id: str, args: list[str]) -> None:
        self._client.send_message(chat_id, _COMMAND_LIST_TEXT)

    def _handle_status(self, chat_id: str, args: list[str]) -> None:
        service = SystemdService(RecorderServiceKind.RECORDER)
        state = "active" if service.is_active() else "inactive"
        target = storage.get_current_recording_target()
        self._client.send_message(
            chat_id,
            f"recorder.service: {state}\nRecording to: {target or 'unknown'}",
        )

    def _handle_drives(self, chat_id: str, args: list[str]) -> None:
        desktop_dir, desktop_target = self._desktop_dirs()
        lines = ["Storage targets:"]
        for target in storage.get_targets(self._config):
            free_gb = storage.get_available_space_gb(target)
            stats = storage.get_recording_stats(target, desktop_dir, desktop_target)
            size_gb = stats.total_bytes / (1024**3)
            lines.append(
                f"{target}: {free_gb:.2f} GB free, "
                f"{stats.count} recordings ({size_gb:.2f} GB)"
            )
        self._client.send_message(chat_id, "\n".join(lines))

    def _handle_preview(self, chat_id: str, args: list[str]) -> None:
        preview = self._find_latest_preview()
        if preview is None:
            self._client.send_message(chat_id, "No preview available yet")
            return
        self._client.send_photo(chat_id, preview, caption=preview.stem)

    def _live_snapshot_ready(self, request_time: float) -> bool:
        try:
            return paths.PATH_LIVE_SNAPSHOT.stat().st_mtime >= request_time
        except OSError:
            return False

    def _handle_live(self, chat_id: str, args: list[str]) -> None:
        if not SystemdService(RecorderServiceKind.RECORDER).is_active():
            self._client.send_message(chat_id, "recorder.service is not running")
            return

        request_time = time.time()
        paths.PATH_DATA_USER.mkdir(parents=True, exist_ok=True)
        paths.PATH_LIVE_SNAPSHOT_REQUEST.touch()

        deadline = request_time + _LIVE_SNAPSHOT_TIMEOUT_SECONDS
        while time.time() < deadline:
            if self._live_snapshot_ready(request_time):
                self._client.send_photo(
                    chat_id, paths.PATH_LIVE_SNAPSHOT, caption="Live"
                )
                return
            if self._shutdown_event.wait(_LIVE_SNAPSHOT_POLL_INTERVAL_SECONDS):
                return

        paths.PATH_LIVE_SNAPSHOT_REQUEST.unlink(missing_ok=True)
        self._client.send_message(chat_id, "Timed out waiting for a live snapshot")

    def _autopush_status_text(self) -> str:
        state = "on" if self._bot_config.auto_push_preview else "off"
        return f"Auto-push preview: {state}"

    def _autopush_keyboard(self) -> InlineKeyboard:
        if self._bot_config.auto_push_preview:
            button = InlineButton(
                text="Disable", callback_data=TelegramCallbackAction.AUTOPUSH_OFF.value
            )
        else:
            button = InlineButton(
                text="Enable", callback_data=TelegramCallbackAction.AUTOPUSH_ON.value
            )
        return InlineKeyboard(rows=[[button]])

    def _handle_autopush(self, chat_id: str, args: list[str]) -> None:
        self._client.send_message(
            chat_id, self._autopush_status_text(), keyboard=self._autopush_keyboard()
        )

    def _set_autopush(self, callback: TelegramCallbackQuery, enabled: bool) -> None:
        self._bot_config = save_telegram_bot_config(
            TelegramBotConfig(auto_push_preview=enabled)
        )
        self._client.answer_callback_query(callback.callback_id)
        self._client.edit_message_text(
            callback.chat_id,
            callback.message_id,
            self._autopush_status_text(),
            keyboard=self._autopush_keyboard(),
        )

    def _on_autopush_on(self, callback: TelegramCallbackQuery, payload: str) -> None:
        self._set_autopush(callback, True)

    def _on_autopush_off(self, callback: TelegramCallbackQuery, payload: str) -> None:
        self._set_autopush(callback, False)

    def _handle_list(self, chat_id: str, args: list[str]) -> None:
        self._send_list_page(chat_id, message_id=None, page=0)

    def _on_list_page(self, callback: TelegramCallbackQuery, payload: str) -> None:
        page = int(payload) if payload.isdigit() else 0
        self._client.answer_callback_query(callback.callback_id)
        self._send_list_page(
            callback.chat_id, message_id=callback.message_id, page=page
        )

    def _send_list_page(self, chat_id: str, message_id: int | None, page: int) -> None:
        desktop_dir, desktop_target = self._desktop_dirs()
        recordings = storage.list_recordings(
            storage.get_targets(self._config), desktop_dir, desktop_target
        )

        if not recordings:
            text = "No recordings found"
            keyboard = None
        else:
            start = page * _LIST_PAGE_SIZE
            page_items = recordings[start : start + _LIST_PAGE_SIZE]
            text = f"Recordings (page {page + 1}):"
            rows: list[list[InlineButton]] = []
            for recording in page_items:
                token = uuid.uuid4().hex[:8]
                self._pending_deletes[token] = recording
                size_mb = recording.stat().st_size / (1024**2)
                rows.append(
                    [
                        InlineButton(
                            text=f"{recording.name} ({size_mb:.0f} MB)",
                            callback_data=(
                                f"{TelegramCallbackAction.DELETE_REQUEST.value}:{token}"
                            ),
                        )
                    ]
                )

            nav_row: list[InlineButton] = []
            if page > 0:
                nav_row.append(
                    InlineButton(
                        text="< Prev",
                        callback_data=(
                            f"{TelegramCallbackAction.LIST_PAGE.value}:{page - 1}"
                        ),
                    )
                )
            if start + _LIST_PAGE_SIZE < len(recordings):
                nav_row.append(
                    InlineButton(
                        text="Next >",
                        callback_data=(
                            f"{TelegramCallbackAction.LIST_PAGE.value}:{page + 1}"
                        ),
                    )
                )
            if nav_row:
                rows.append(nav_row)
            keyboard = InlineKeyboard(rows=rows)

        if message_id is None:
            self._client.send_message(chat_id, text, keyboard=keyboard)
        else:
            self._client.edit_message_text(chat_id, message_id, text, keyboard=keyboard)

    def _on_delete_request(self, callback: TelegramCallbackQuery, payload: str) -> None:
        recording = self._pending_deletes.get(payload)
        if recording is None:
            self._client.answer_callback_query(
                callback.callback_id, "Expired, run /list again"
            )
            return

        self._client.answer_callback_query(callback.callback_id)
        keyboard = InlineKeyboard(
            rows=[
                [
                    InlineButton(
                        text="Yes, delete",
                        callback_data=(
                            f"{TelegramCallbackAction.DELETE_CONFIRM.value}:{payload}"
                        ),
                    ),
                    InlineButton(
                        text="Cancel",
                        callback_data=(
                            f"{TelegramCallbackAction.DELETE_CANCEL.value}:{payload}"
                        ),
                    ),
                ]
            ]
        )
        self._client.edit_message_text(
            callback.chat_id,
            callback.message_id,
            f"Delete {recording.name}?",
            keyboard=keyboard,
        )

    def _on_delete_confirm(self, callback: TelegramCallbackQuery, payload: str) -> None:
        recording = self._pending_deletes.pop(payload, None)
        self._client.answer_callback_query(callback.callback_id)
        if recording is None:
            self._client.edit_message_text(
                callback.chat_id, callback.message_id, "Expired, run /list again"
            )
            return

        storage.delete_recording_pair(recording)
        self._client.edit_message_text(
            callback.chat_id, callback.message_id, f"Deleted {recording.name}"
        )

    def _on_delete_cancel(self, callback: TelegramCallbackQuery, payload: str) -> None:
        self._pending_deletes.pop(payload, None)
        self._client.answer_callback_query(callback.callback_id)
        self._client.edit_message_text(
            callback.chat_id, callback.message_id, "Cancelled"
        )

    def _resolve_clear_targets(self, payload: str) -> list[Path] | None:
        targets = storage.get_targets(self._config)
        if payload == "all":
            return targets
        if payload.isdigit() and int(payload) < len(targets):
            return [targets[int(payload)]]
        return None

    def _handle_clear(self, chat_id: str, args: list[str]) -> None:
        targets = storage.get_targets(self._config)
        rows = [
            [
                InlineButton(
                    text=str(target),
                    callback_data=f"{TelegramCallbackAction.CLEAR_SELECT.value}:{index}",
                )
            ]
            for index, target in enumerate(targets)
        ]
        rows.append(
            [
                InlineButton(
                    text="All drives",
                    callback_data=f"{TelegramCallbackAction.CLEAR_SELECT.value}:all",
                )
            ]
        )
        self._client.send_message(
            chat_id, "Clear recordings from:", keyboard=InlineKeyboard(rows=rows)
        )

    def _on_clear_select(self, callback: TelegramCallbackQuery, payload: str) -> None:
        targets = self._resolve_clear_targets(payload)
        if targets is None:
            self._client.answer_callback_query(
                callback.callback_id, "Invalid selection, run /clear again"
            )
            return

        self._client.answer_callback_query(callback.callback_id)
        desktop_dir, desktop_target = self._desktop_dirs()
        count = 0
        total_bytes = 0
        for target in targets:
            stats = storage.get_recording_stats(target, desktop_dir, desktop_target)
            count += stats.count
            total_bytes += stats.total_bytes
        size_gb = total_bytes / (1024**3)

        keyboard = InlineKeyboard(
            rows=[
                [
                    InlineButton(
                        text=f"Yes, delete all {count}",
                        callback_data=(
                            f"{TelegramCallbackAction.CLEAR_CONFIRM.value}:{payload}"
                        ),
                    ),
                    InlineButton(
                        text="Cancel",
                        callback_data=(
                            f"{TelegramCallbackAction.CLEAR_CANCEL.value}:{payload}"
                        ),
                    ),
                ]
            ]
        )
        self._client.edit_message_text(
            callback.chat_id,
            callback.message_id,
            f"This deletes {count} recordings ({size_gb:.2f} GB). Cannot be undone.",
            keyboard=keyboard,
        )

    def _on_clear_confirm(self, callback: TelegramCallbackQuery, payload: str) -> None:
        targets = self._resolve_clear_targets(payload)
        self._client.answer_callback_query(callback.callback_id)
        if targets is None:
            self._client.edit_message_text(
                callback.chat_id,
                callback.message_id,
                "Invalid selection, run /clear again",
            )
            return

        desktop_dir, desktop_target = self._desktop_dirs()
        deleted = 0
        for target in targets:
            for recording in storage.list_recordings(
                [target], desktop_dir, desktop_target
            ):
                storage.delete_recording_pair(recording)
                deleted += 1

        self._client.edit_message_text(
            callback.chat_id, callback.message_id, f"Deleted {deleted} recordings"
        )

    def _on_clear_cancel(self, callback: TelegramCallbackQuery, payload: str) -> None:
        self._client.answer_callback_query(callback.callback_id)
        self._client.edit_message_text(
            callback.chat_id, callback.message_id, "Cancelled"
        )

    def _handle_restart(self, chat_id: str, args: list[str]) -> None:
        keyboard = InlineKeyboard(
            rows=[
                [
                    InlineButton(
                        text="Yes, restart",
                        callback_data=TelegramCallbackAction.RESTART_CONFIRM.value,
                    ),
                    InlineButton(
                        text="Cancel",
                        callback_data=TelegramCallbackAction.RESTART_CANCEL.value,
                    ),
                ]
            ]
        )
        self._client.send_message(
            chat_id, "Restart recorder.service?", keyboard=keyboard
        )

    def _on_restart_confirm(
        self, callback: TelegramCallbackQuery, payload: str
    ) -> None:
        self._client.answer_callback_query(callback.callback_id)
        try:
            SystemdService(RecorderServiceKind.RECORDER).restart()
        except RuntimeError as exc:
            self._client.edit_message_text(
                callback.chat_id, callback.message_id, f"Restart failed: {exc}"
            )
            return
        self._client.edit_message_text(
            callback.chat_id, callback.message_id, "recorder.service restarted"
        )

    def _on_restart_cancel(self, callback: TelegramCallbackQuery, payload: str) -> None:
        self._client.answer_callback_query(callback.callback_id)
        self._client.edit_message_text(
            callback.chat_id, callback.message_id, "Cancelled"
        )

    def _handle_logs(self, chat_id: str, args: list[str]) -> None:
        kind = (
            RecorderServiceKind.TELEGRAM
            if args[:1] == ["telegram"]
            else RecorderServiceKind.RECORDER
        )
        output = SystemdService(kind).logs(_LOG_LINES)
        if not output.strip():
            self._client.send_message(chat_id, f"No logs for {kind.service_name}")
            return

        if len(output) <= _MAX_MESSAGE_CHARS:
            self._client.send_message(chat_id, f"```\n{output}\n```")
            return

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as temp_file:
            temp_file.write(output)
            temp_path = Path(temp_file.name)
        try:
            self._client.send_document(
                chat_id, temp_path, caption=f"{kind.service_name} logs"
            )
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _system_uptime_seconds() -> float | None:
        try:
            return float(Path("/proc/uptime").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _format_duration(seconds: float | None) -> str:
        if seconds is None:
            return "unknown"
        total_minutes = int(seconds // 60)
        days, remainder_minutes = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(remainder_minutes, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours or days:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)

    def _handle_uptime(self, chat_id: str, args: list[str]) -> None:
        system_seconds = self._system_uptime_seconds()
        service_seconds = SystemdService(
            RecorderServiceKind.RECORDER
        ).active_since_seconds()
        lines = [f"System uptime: {self._format_duration(system_seconds)}"]
        if service_seconds is not None:
            lines.append(
                f"recorder.service uptime: {self._format_duration(service_seconds)}"
            )
        else:
            lines.append("recorder.service uptime: not running")
        self._client.send_message(chat_id, "\n".join(lines))
