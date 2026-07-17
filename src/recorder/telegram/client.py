from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(kw_only=True, slots=True, frozen=True)
class InlineButton:
    text: str
    callback_data: str

    def to_dict(self) -> dict[str, str]:
        return {"text": self.text, "callback_data": self.callback_data}


@dataclass(kw_only=True, slots=True, frozen=True)
class InlineKeyboard:
    rows: list[list[InlineButton]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [button.to_dict() for button in row] for row in self.rows
            ]
        }


@dataclass(kw_only=True, slots=True, frozen=True)
class TelegramMessage:
    chat_id: str
    text: str | None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TelegramMessage:
        return cls(chat_id=str(payload["chat"]["id"]), text=payload.get("text"))


@dataclass(kw_only=True, slots=True, frozen=True)
class TelegramCallbackQuery:
    callback_id: str
    data: str
    chat_id: str
    message_id: int

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TelegramCallbackQuery:
        message = payload["message"]
        return cls(
            callback_id=payload["id"],
            data=payload.get("data", ""),
            chat_id=str(message["chat"]["id"]),
            message_id=message["message_id"],
        )


@dataclass(kw_only=True, slots=True, frozen=True)
class TelegramUpdate:
    update_id: int
    message: TelegramMessage | None
    callback_query: TelegramCallbackQuery | None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TelegramUpdate:
        message_payload = payload.get("message")
        callback_payload = payload.get("callback_query")
        return cls(
            update_id=payload["update_id"],
            message=TelegramMessage.from_dict(message_payload)
            if message_payload
            else None,
            callback_query=TelegramCallbackQuery.from_dict(callback_payload)
            if callback_payload
            else None,
        )


class TelegramClient:
    def __init__(self, bot_token: str) -> None:
        self._http = httpx.Client(
            base_url=f"https://api.telegram.org/bot{bot_token}",
            timeout=httpx.Timeout(15.0, read=35.0),
        )

    def close(self) -> None:
        self._http.close()

    def send_message(
        self, chat_id: str, text: str, keyboard: InlineKeyboard | None = None
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if keyboard is not None:
            payload["reply_markup"] = keyboard.to_dict()
        response = self._http.post("/sendMessage", json=payload)
        response.raise_for_status()

    def send_photo(
        self, chat_id: str, photo_path: Path, caption: str | None = None
    ) -> None:
        data = {"chat_id": chat_id}
        if caption is not None:
            data["caption"] = caption
        with photo_path.open("rb") as photo_file:
            files = {"photo": (photo_path.name, photo_file, "image/jpeg")}
            response = self._http.post("/sendPhoto", data=data, files=files)
        response.raise_for_status()

    def send_document(
        self, chat_id: str, document_path: Path, caption: str | None = None
    ) -> None:
        data = {"chat_id": chat_id}
        if caption is not None:
            data["caption"] = caption
        with document_path.open("rb") as document_file:
            files = {"document": (document_path.name, document_file, "text/plain")}
            response = self._http.post("/sendDocument", data=data, files=files)
        response.raise_for_status()

    def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        keyboard: InlineKeyboard | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if keyboard is not None:
            payload["reply_markup"] = keyboard.to_dict()
        response = self._http.post("/editMessageText", json=payload)
        response.raise_for_status()

    def answer_callback_query(self, callback_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text is not None:
            payload["text"] = text
        response = self._http.post("/answerCallbackQuery", json=payload)
        response.raise_for_status()

    def get_updates(self, offset: int, timeout_seconds: int) -> list[TelegramUpdate]:
        response = self._http.get(
            "/getUpdates",
            params={"offset": offset, "timeout": timeout_seconds},
            timeout=httpx.Timeout(15.0, read=timeout_seconds + 10),
        )
        response.raise_for_status()
        return [TelegramUpdate.from_dict(item) for item in response.json()["result"]]
