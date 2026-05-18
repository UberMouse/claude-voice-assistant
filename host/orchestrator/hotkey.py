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

def _parse_hotkey(spec: str):
    """Parse a hotkey spec like 'f3' or 'lshift+f3' into (modifiers, trigger).

    Tokens are joined by '+'. The last token is the trigger key; earlier tokens
    are modifiers. Returns a list of modifier-name groups (each group is a
    frozenset of pynput Key attribute names that satisfy that modifier — e.g.
    'shift' matches either shift_l or shift_r) and the trigger Key-attribute
    name. Resolution to actual pynput Key objects happens at runtime so this
    helper stays pure and unit-testable without importing pynput.
    """
    tokens = [t.strip().lower() for t in spec.split("+") if t.strip()]
    if not tokens:
        raise ValueError(f"empty hotkey: {spec!r}")
    *mod_tokens, trigger = tokens

    aliases = {
        "shift":  ("shift_l", "shift_r"),
        "lshift": ("shift_l",),
        "rshift": ("shift_r",),
        "ctrl":   ("ctrl_l", "ctrl_r"),
        "lctrl":  ("ctrl_l",),
        "rctrl":  ("ctrl_r",),
        "alt":    ("alt_l", "alt_r", "alt_gr"),
        "lalt":   ("alt_l",),
        "ralt":   ("alt_r", "alt_gr"),
        "cmd":    ("cmd", "cmd_l", "cmd_r"),
        "win":    ("cmd", "cmd_l", "cmd_r"),
        "super":  ("cmd", "cmd_l", "cmd_r"),
    }
    modifier_groups = []
    for m in mod_tokens:
        if m not in aliases:
            raise ValueError(f"unknown modifier: {m!r}")
        modifier_groups.append(frozenset(aliases[m]))
    return modifier_groups, trigger


def run_pynput(key: str, dispatcher: HotkeyDispatcher) -> None:
    """Blocking pynput loop. Maps key (e.g. 'f3' or 'lshift+f3') to the
    dispatcher. For chords, the trigger key only fires while all required
    modifiers are held; release fires regardless of modifier state so we never
    get stuck in a 'pressed' state."""
    from pynput import keyboard

    modifier_groups, trigger_name = _parse_hotkey(key)
    target_key = getattr(keyboard.Key, trigger_name, None)
    if target_key is None:
        raise ValueError(f"unknown key: {trigger_name}")

    # Resolve modifier groups to concrete pynput Key objects.
    resolved_groups = []
    for group in modifier_groups:
        keys = {getattr(keyboard.Key, name) for name in group if hasattr(keyboard.Key, name)}
        if not keys:
            raise ValueError(f"no pynput keys for modifier group: {sorted(group)}")
        resolved_groups.append(keys)

    held_mods: set = set()
    pressed = False  # whether we've dispatched a press we haven't released yet

    def now_ms() -> int:
        return int(time.perf_counter() * 1000)

    def all_mods_held() -> bool:
        return all(held_mods & group for group in resolved_groups)

    def on_press(k):
        nonlocal pressed
        if any(k in group for group in resolved_groups):
            held_mods.add(k)
            return
        if k == target_key and not pressed and all_mods_held():
            pressed = True
            dispatcher._on_press(now_ms())

    def on_release(k):
        nonlocal pressed
        if any(k in group for group in resolved_groups):
            held_mods.discard(k)
            return
        if k == target_key and pressed:
            pressed = False
            dispatcher._on_release(now_ms())

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()
