"""Pure-logic tests for the Endpointer state machine. No torch, no audio I/O."""
from host.audio.vad import Endpointer


def _feed(ep: Endpointer, scores):
    """Feed a list of scores and return the EndpointResult (or None) at the end."""
    last = None
    for s in scores:
        last = ep.feed(s)
    return last


def test_silence_triggers_after_speech():
    # 32ms frames; 800ms silence threshold = 25 frames
    ep = Endpointer(silence_ms=800, max_ms=60_000, no_speech_ms=10_000)
    speech_run = [0.9] * 10           # ~320ms of speech
    silence_run = [0.1] * 30          # ~960ms of silence -> should trigger by frame ~25
    result = _feed(ep, speech_run + silence_run)
    assert result is not None
    assert result.reason == "silence"
    assert result.last_speech_ms == 10 * ep.frame_ms


def test_silence_does_not_trigger_before_speech():
    """no_speech path: never crossed the threshold; should fire `no_speech`, not `silence`."""
    ep = Endpointer(silence_ms=800, max_ms=60_000, no_speech_ms=1_000)
    # 1000ms / 32ms ≈ 32 frames -> no_speech should fire around there
    result = _feed(ep, [0.05] * 50)
    assert result is not None
    assert result.reason == "no_speech"


def test_max_duration_caps_long_speech():
    """Continuous speech with no trailing silence: max_duration cap fires."""
    ep = Endpointer(silence_ms=800, max_ms=320, no_speech_ms=10_000)
    # max_ms=320 / frame_ms=32 ≈ 10 frames
    result = _feed(ep, [0.9] * 20)
    assert result is not None
    assert result.reason == "max_duration"


def test_threshold_boundary():
    """Scores exactly at the threshold count as speech."""
    ep = Endpointer(speech_threshold=0.5, silence_ms=64, max_ms=10_000, no_speech_ms=10_000)
    # one frame at exactly threshold should trigger; then a couple silent frames end it
    result = _feed(ep, [0.5, 0.0, 0.0, 0.0])
    assert result is not None
    assert result.reason == "silence"


def test_feed_after_done_is_idempotent():
    ep = Endpointer(silence_ms=64, max_ms=10_000, no_speech_ms=10_000)
    first = _feed(ep, [0.9, 0.0, 0.0, 0.0])
    assert first is not None
    second = ep.feed(0.9)
    assert second is first


def test_reset_clears_state():
    ep = Endpointer(silence_ms=64, max_ms=10_000, no_speech_ms=10_000)
    _feed(ep, [0.9, 0.0, 0.0, 0.0])
    assert ep.result is not None
    ep.reset()
    assert ep.result is None
    assert ep.feed(0.0) is None  # fresh state, single silent frame can't end it
