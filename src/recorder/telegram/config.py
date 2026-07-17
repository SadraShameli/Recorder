from pydantic import BaseModel

from .. import paths


class TelegramBotConfig(BaseModel):
    auto_push_preview: bool = False


def get_telegram_bot_config() -> TelegramBotConfig:
    if not paths.PATH_CONFIG.exists():
        return save_telegram_bot_config(TelegramBotConfig())
    return TelegramBotConfig.model_validate_json(paths.PATH_CONFIG.read_text())


def save_telegram_bot_config(config: TelegramBotConfig) -> TelegramBotConfig:
    paths.PATH_DATA_USER.mkdir(parents=True, exist_ok=True)
    paths.PATH_CONFIG.write_text(config.model_dump_json(indent=4))
    return config
