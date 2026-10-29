"""Read-only local preflight. No API calls, browser launch, or publishing."""

import importlib
import importlib.metadata
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parent


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / '.env')
    failures = []

    def report(name: str, ok: bool, detail: str) -> None:
        print(f"{'OK' if ok else 'FAIL'} {name}: {detail}")
        if not ok:
            failures.append(name)

    report('Python', sys.version_info >= (3, 11), sys.executable)
    for name in ('temp', 'output', 'models', 'uploaded_clips', 'selector_audit'):
        directory = ROOT / name
        target = directory if directory.exists() else ROOT
        report(f'Writable {name}', os.access(target, os.W_OK),
               'writable by current user' if os.access(target, os.W_OK) else 'fix ownership/permissions for the service user')
    for module, package in (
        ('streamlit', 'streamlit'), ('psutil', 'psutil'),
        ('faster_whisper', 'faster-whisper'), ('google.genai', 'google-genai'),
        ('mediapipe.tasks.python.vision', 'mediapipe'), ('cv2', 'opencv-python-headless'),
        ('streamlink', 'streamlink'), ('yt_dlp', 'yt-dlp'),
        ('playwright.sync_api', 'playwright'), ('filelock', 'filelock'),
    ):
        try:
            importlib.import_module(module)
            report(package, True, importlib.metadata.version(package))
        except Exception as exc:
            report(package, False, f'{type(exc).__name__}; run uv sync --locked')

    for binary in ('ffmpeg', 'ffprobe'):
        path = shutil.which(binary)
        ok = False
        if path:
            try:
                ok = subprocess.run([path, '-version'], capture_output=True, timeout=10).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        report(binary, ok, path or 'missing from PATH')

    # Imports alone miss incompatible PyAV releases (faster-whisper issue #1589).
    try:
        from faster_whisper.audio import decode_audio
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b'\x00\x00' * 800)
        buffer.seek(0)
        samples = decode_audio(buffer, sampling_rate=16000)
        report('Whisper audio decoder', len(samples) == 1600, 'real WAV decode/resampling')
    except Exception as exc:
        report('Whisper audio decoder', False, f'{type(exc).__name__}; run uv sync --locked')

    key = os.environ.get('GEMINI_API_KEY', '').strip()
    report('GEMINI_API_KEY', bool(key and key != '...'), 'configured' if key and key != '...' else 'missing in .env')
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            installed = Path(pw.chromium.executable_path).is_file()
        report('Chromium', installed, 'installed' if installed else 'run uv run playwright install chromium')
    except Exception as exc:
        report('Chromium', False, type(exc).__name__)

    print('TikTok session validity is separate: see README_UPLOAD.md. No upload was attempted.')
    print(f'{len(failures)} failed check(s).')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
