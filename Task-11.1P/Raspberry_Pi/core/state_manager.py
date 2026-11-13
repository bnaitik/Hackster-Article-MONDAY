from enum import Enum
from threading import RLock

class BotState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    STUDYING = "studying"
    ALARM = "alarm"
    ERROR = "error"

class StateManager:
    def __init__(self):
        self._state = BotState.IDLE
        self._lock = RLock()

    @property
    def current_state(self) -> BotState:
        with self._lock:
            return self._state

    def set_state(self, new_state: BotState) -> None:
        if not isinstance(new_state, BotState):
            raise ValueError("new_state must be a BotState value")

        with self._lock:
            self._state = new_state
