import respx, httpx, pytest
from vm.speak.cli import speak

@respx.mock
def test_speak_posts_text():
    route = respx.post("http://127.0.0.1:8002/speak").mock(return_value=httpx.Response(202, json={"queued": True}))
    rc = speak("hello world", url="http://127.0.0.1:8002")
    assert rc == 0
    assert route.called
    assert route.calls.last.request.read() == b'{"text": "hello world"}'

@respx.mock
def test_speak_handles_failure():
    respx.post("http://127.0.0.1:8002/speak").mock(return_value=httpx.Response(500))
    rc = speak("hi", url="http://127.0.0.1:8002")
    assert rc != 0
