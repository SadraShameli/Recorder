from pathlib import Path


class UtilPath:
    @staticmethod
    def get_root_path() -> Path:
        current = Path(__file__).resolve().parent
        while current != current.parent:
            if (current / "pyproject.toml").exists():
                return current
            current = current.parent
        return Path.cwd()


PATH_ROOT = UtilPath.get_root_path()
PATH_DATA_USER = PATH_ROOT / "data_user"
PATH_CONFIG = PATH_DATA_USER / "config.json"
PATH_STATE = PATH_DATA_USER / "recorder_state.json"
