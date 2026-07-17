from enum import Enum


class TelegramCommand(Enum):
    START = "/start"
    HELP = "/help"
    STATUS = "/status"
    DRIVES = "/drives"
    PREVIEW = "/preview"
    AUTOPUSH = "/autopush"
    LIST = "/list"
    CLEAR = "/clear"
    RESTART = "/restart"
    LOGS = "/logs"
    UPTIME = "/uptime"


class TelegramCallbackAction(Enum):
    LIST_PAGE = "list"
    DELETE_REQUEST = "del"
    DELETE_CONFIRM = "delyes"
    DELETE_CANCEL = "delno"
    CLEAR_SELECT = "clearsel"
    CLEAR_CONFIRM = "clearyes"
    CLEAR_CANCEL = "clearno"
    AUTOPUSH_ON = "autoon"
    AUTOPUSH_OFF = "autooff"
    RESTART_CONFIRM = "restartyes"
    RESTART_CANCEL = "restartno"
