import pytest

from host.orchestrator.hotkey import HotkeyDispatcher, _parse_hotkey


def test_dispatcher_fires_press_then_release():
    events = []
    d = HotkeyDispatcher(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
    )
    d._on_press()
    d._on_release()
    assert events == ["press", "release"]


def test_duplicate_press_is_ignored_until_release():
    """A real keyboard auto-repeats while held; pynput delivers a second press
    event but we should not start a second cycle for the same hold."""
    events = []
    d = HotkeyDispatcher(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
    )
    d._on_press()
    d._on_press()
    d._on_release()
    assert events == ["press", "release"]


def test_release_without_matching_press_is_ignored():
    events = []
    d = HotkeyDispatcher(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
    )
    d._on_release()
    assert events == []


def test_press_release_then_press_release_again():
    events = []
    d = HotkeyDispatcher(
        on_press=lambda: events.append("p"),
        on_release=lambda: events.append("r"),
    )
    d._on_press()
    d._on_release()
    d._on_press()
    d._on_release()
    assert events == ["p", "r", "p", "r"]


def test_parse_single_key():
    mods, trigger = _parse_hotkey("f3")
    assert mods == []
    assert trigger == "f3"


def test_parse_chord_lshift_f3():
    mods, trigger = _parse_hotkey("lshift+f3")
    assert trigger == "f3"
    assert mods == [frozenset({"shift_l"})]


def test_parse_chord_generic_shift_matches_either_side():
    mods, _ = _parse_hotkey("shift+f3")
    assert mods == [frozenset({"shift_l", "shift_r"})]


def test_parse_multi_modifier_preserves_order():
    mods, trigger = _parse_hotkey("ctrl+alt+f3")
    assert trigger == "f3"
    assert mods == [
        frozenset({"ctrl_l", "ctrl_r"}),
        frozenset({"alt_l", "alt_r", "alt_gr"}),
    ]


def test_parse_rejects_unknown_modifier():
    with pytest.raises(ValueError):
        _parse_hotkey("hyper+f3")


def test_parse_is_case_insensitive_and_strips_spaces():
    mods, trigger = _parse_hotkey(" LShift + F3 ")
    assert trigger == "f3"
    assert mods == [frozenset({"shift_l"})]
