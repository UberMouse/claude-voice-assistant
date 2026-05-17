"""`speak <text>` — Claude calls this as a tool to speak via the host TTS server."""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import httpx

log = logging.getLogger(__name__)

# DEBUG-TAG: speak-cli
# Grep: grep -E "speak-cli"

def speak(text: str, url: str | None = None, timeout: float = 5.0) -> int:
    url = url or os.environ.get("VOICE_TTS_URL", "http://127.0.0.1:8002")
    try:
        body = json.dumps({"text": text})
        r = httpx.post(
            f"{url.rstrip('/')}/speak",
            content=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        r.raise_for_status()
        log.info("speak-cli: queued %r", text[:60])
        return 0
    except Exception as e:
        log.error("speak-cli: failed: %s", e)
        return 1

def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Speak text via the voice-assistant TTS server")
    ap.add_argument("text", help="Text to speak. Use quotes for multi-word strings.")
    ap.add_argument("--url", default=None, help="TTS server URL (defaults to $VOICE_TTS_URL or localhost:8002)")
    args = ap.parse_args()
    sys.exit(speak(args.text, url=args.url))


if __name__ == "__main__":
    main()
