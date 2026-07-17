import os

from dotenv import load_dotenv

from .. import paths

load_dotenv(str(paths.PATH_ROOT / ".env"), override=True)


class Env:
    @staticmethod
    def telegram_bot_token() -> str:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN not set; create a bot via @BotFather and "
                "add it to .env"
            )
        return token

    @staticmethod
    def telegram_bot_chat_ids() -> frozenset[str]:
        raw = os.environ.get("TELEGRAM_BOT_CHAT_IDS", "")
        ids = frozenset(
            chat_id.strip() for chat_id in raw.split(",") if chat_id.strip()
        )
        if not ids:
            raise RuntimeError(
                "TELEGRAM_BOT_CHAT_IDS not set; the bot would authorize nobody"
            )
        return ids
