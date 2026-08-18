# CLAUDE.md — Auto-Clipping & Stream-Monitoring Pipeline Rules

## Always Do First
- **Check Environment:** Always verify that the virtual environment (`venv`) is active and `.env` variables (API keys) are loaded before running scripts.
- **Modularity Check:** Ensure any new code fits into the modular pipeline. Do not mix domains. UI features must go through `dashboard_api.py`, not directly into backend logic.

## Core Architecture & 24/7 Workflow
The system has evolved from a manual script to an autonomous 24/7 supervisor-driven pipeline.
- **Phase 1 (Supervision & Monitoring):** `process_supervisor.py` manages `orchestrator.py` and `metrics_tracker.py`. Real-time stream ingestion is handled by `stream_watcher.py` (Twitch).
- **Phase 2 (Analysis & Vision Critic):** `auto_pilot.py` manages Audio Extraction -> Whisper Transcription -> LLM Analysis -> `train_loop.py` (Vision Critic / AI Feedback loop).
- **Phase 3 (Render & Output):** `process.py` uses FFmpeg for rendering -> `tiktok_uploader.py` handles Playwright automation.
- **Dashboard:** `app.py` (Streamlit) provides read-only oversight and configuration, strictly decoupled via `dashboard_api.py`.

## TikTok Upload Safety Model (CRITICAL)
- **No Implicit Drafts:** TikTok Web no longer reliably saves drafts when closing the browser without clicking anything. 
- **Upload Rule:** DO NOT trigger `tiktok_uploader.py` or the Playwright browser unless `--publish` (or `auto-upload`) is explicitly set to `True`.
- **Local Fallback:** If a clip is generated but not set to auto-publish, it MUST remain locally on the disk (`/output`). Do not attempt to push it to TikTok Studio as a draft.
- **Overlays:** Playwright automation must always aggressively check for and dismiss intercepting overlays (Cookie Banners, Onboarding Tooltips, Shadow DOM elements) before clicking elements.

## Data & State Integrity (CRITICAL)
- **Atomic Writes:** All state files (`agent_state.json`, `orchestrator_state.json`, `clips.json`, `viral_memory.json`, `ai_guidelines.txt`, `cookies.json`) MUST be written using the temporary-then-replace pattern (e.g., via `atomic_io.py`). Never use direct `open(path, "w")` to avoid torn reads or corruption on crashes.
- **Unique IDs:** Clip titles must be enforced as unique keys to prevent silent batch overwrites.

## Local Hardware & Processing Limits
- **CPU & GPU Constraints:** The system runs on 32GB RAM with an Intel integrated GPU. DO NOT use CUDA-exclusive libraries.
- **Local AI (Text/Audio):** Use CPU-optimized libraries like `faster-whisper` (CTranslate2) or Intel OpenVINO for local speech-to-text.
- **Local AI (Vision):** Use `mediapipe` (Tasks API) for lightweight, CPU-based face detection and dynamic cropping.
- **Concurrency:** Audio transcription and frame extraction should be parallelized where possible; final FFmpeg video rendering remains serial to avoid iGPU congestion.

## Video Output Defaults
- **Format:** 9:16 vertical video (1080x1920 resolution).
- **Layout:** Split-screen. Facecam is dynamically detected via MediaPipe, padded, and placed at the top half; gameplay/main content is cropped to the bottom half.
- **Subtitles:** `.ass` (Advanced SubStation Alpha) format for dynamic, word-by-word highlighting. Hardcoded into the final video via FFmpeg.
- **Framerate:** Match the source video framerate (usually 30 or 60 fps).

## Git & Security (CRITICAL)
- **Auto-Commit Rule:** At the end of every successful task/feature, automatically stage changes (`git add .`), create a Conventional Commit, and run `git push`.
- **Secret Protection:** NEVER commit `.env`, `client_secret.json`, `tiktok_client_secret.json`, `tiktok_token.json`, or `cookies.json`. Always ensure they are in `.gitignore`.
- **Large & Temp Files:** NEVER commit `temp/`, `output/`, `selector_audit/`, or any raw media (`*.mp4`, `*.wav`, `*.ts`).
- **Log Sanitation:** Ensure all OpenAI/Anthropic API keys and Bearer tokens are redacted from logs before printing or saving.

## Hard Rules
- Do not introduce heavy C++ compilation dependencies unless absolutely necessary.
- Do not overwrite the original source video files.
- Do not proceed to the next pipeline step if the previous step's output fails validation.
- Do not hallucinate timestamps. The LLM analysis must only select timestamps that exist exactly in the Whisper transcription data.