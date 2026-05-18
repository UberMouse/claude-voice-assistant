import logging, os, uvicorn
from .server import build_app

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host = os.environ.get("VOICE_TTS_HOST", "127.0.0.1")
    port = int(os.environ.get("VOICE_TTS_PORT", "8002"))
    voice = os.environ.get("VOICE_TTS_VOICE", "af_sarah")
    speed = float(os.environ.get("VOICE_TTS_SPEED", "1.0"))
    uvicorn.run(build_app(voice_default=voice, speed_default=speed), host=host, port=port)


if __name__ == "__main__":
    main()
