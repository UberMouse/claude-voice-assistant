import asyncio
import pytest
from host.tts.queue import PlaybackQueue

@pytest.mark.asyncio
async def test_queue_serializes_playback():
    order = []
    async def fake_play(text: str):
        order.append(("start", text))
        await asyncio.sleep(0.05)
        order.append(("end", text))
    q = PlaybackQueue(play_fn=fake_play)
    await q.start()
    await q.enqueue("a")
    await q.enqueue("b")
    await q.drain()
    await q.stop()
    assert order == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]
