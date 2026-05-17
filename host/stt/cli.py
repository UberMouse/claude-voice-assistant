import os
import sys
import logging
import uvicorn
from .server import build_app

# DEBUG-TAG: stt-server

def _ensure_windows_cuda_dlls_on_path() -> None:
    """Spike B finding: ctranslate2's native loader on Windows ignores
    os.add_dll_directory(). Pip-installed nvidia DLLs (cublas, cudnn, ...) live
    in `site-packages/nvidia/*/bin` and must be on PATH *before* Python starts
    or the loader can't find them. We re-exec ourselves once with PATH prefixed
    so subsequent imports of faster_whisper/ctranslate2 succeed.

    No-op on non-Windows. No-op if we've already re-exec'd (guard env var).
    """
    if sys.platform != "win32" or os.environ.get("VOICE_STT_CUDA_PREFIXED") == "1":
        return
    import site
    extra = []
    for site_dir in site.getsitepackages() + [site.getusersitepackages()]:
        nvidia_root = os.path.join(site_dir, "nvidia")
        if not os.path.isdir(nvidia_root):
            continue
        for pkg in os.listdir(nvidia_root):
            bin_dir = os.path.join(nvidia_root, pkg, "bin")
            if os.path.isdir(bin_dir):
                extra.append(bin_dir)
    if not extra:
        return
    new_path = os.pathsep.join(extra + [os.environ.get("PATH", "")])
    env = {**os.environ, "PATH": new_path, "VOICE_STT_CUDA_PREFIXED": "1"}
    # On Windows, os.execvpe replaces this process so subsequent loader calls
    # see the prefixed PATH from process start. We can't use sys.argv here:
    # pip/uv console-script .exe wrappers report a bare script name (e.g.
    # "voice-stt") as argv[0], so re-running python with that argv would have
    # python try to open "voice-stt" as a file. Re-enter via -c instead.
    os.execvpe(sys.executable, [sys.executable, "-c", "from host.stt.cli import main; main()"], env)

def main():
    _ensure_windows_cuda_dlls_on_path()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    model = os.environ.get("VOICE_STT_MODEL", "distil-large-v3")
    device = os.environ.get("VOICE_STT_DEVICE", "auto")
    host = os.environ.get("VOICE_STT_HOST", "127.0.0.1")
    port = int(os.environ.get("VOICE_STT_PORT", "8001"))
    uvicorn.run(build_app(model, device), host=host, port=port)
