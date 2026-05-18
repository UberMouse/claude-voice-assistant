import pytest

from host.orchestrator.hotkey import HotkeyDispatcher, PressKind, _parse_hotkey

def test_short_press_classification():
    events = []
    d = HotkeyDispatcher(short_press_ms=300, on_event=events.append)
    d._on_press(t_ms=0)
    d._on_release(t_ms=100)
    assert events == [PressKind.SHORT]

def test_long_press_classification():
    events = []
    d = HotkeyDispatcher(short_press_ms=300, on_event=events.append)
    d._on_press(t_ms=0)
    d._on_release(t_ms=500)
    assert events == [PressKind.LONG]


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
