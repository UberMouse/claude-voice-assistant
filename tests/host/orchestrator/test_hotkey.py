from host.orchestrator.hotkey import HotkeyDispatcher, PressKind

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
