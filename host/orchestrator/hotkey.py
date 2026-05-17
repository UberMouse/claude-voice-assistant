"""Hotkey dispatcher. Wraps pynput at runtime, but the classification is pure."""
from __future__ import annotations
import enum
import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

# DEBUG-TAG: hotkey
# Grep: grep -E "hotkey"

class PressKind(str, enum.Enum):
    SHORT = "short"
    LONG = "long"

class HotkeyDispatcher:
    def __init__(self, short_press_ms: int = 300, on_event: Optional[Callable[[PressKind], None]] = None):
        self._short_ms = short_press_ms
        self._on_event = on_event or (lambda _: None)
        self._press_ts_ms: Optional[int] = None
        self._lock = threading.Lock()

    def _on_press(self, t_ms: int) -> None:
        with self._lock:
            if self._press_ts_ms is None:
                self._press_ts_ms = t_ms
                log.debug("hotkey: press at %d", t_ms)

    def _on_release(self, t_ms: int) -> None:
        with self._lock:
            if self._press_ts_ms is None:
                return
            held = t_ms - self._press_ts_ms
            self._press_ts_ms = None
        kind = PressKind.LONG if held >= self._short_ms else PressKind.SHORT
        log.info("hotkey: %s press, held=%dms", kind, held)
        self._on_event(kind)

def run_pynput(key: str, dispatcher: HotkeyDispatcher) -> None:
    """Blocking pynput loop. Maps key (e.g. 'f8') to the dispatcher."""
    from pynput import keyboard

    target_key = getattr(keyboard.Key, key.lower(), None)
    if target_key is None:
        raise ValueError(f"unknown key: {key}")

    def now_ms() -> int:
        return int(time.perf_counter() * 1000)

    def on_press(k):
        if k == target_key:
            dispatcher._on_press(now_ms())

    def on_release(k):
        if k == target_key:
            dispatcher._on_release(now_ms())

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()
