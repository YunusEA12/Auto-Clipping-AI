# CLAUDE.md — Auto-Clipping Pipeline Rules

## Always Do First
- **Check Environment:** Always verify that the virtual environment (`venv`) is active and `.env` variables (API keys) are loaded before running scripts.
- **Modularity Check:** Ensure any new code fits into the 4-step pipeline: Ingestion -> Transcription -> Analysis -> Processing. Do not mix these domains in a single file.

## Local Hardware & Processing Limits
- **CPU & GPU Constraints:** The system runs on 32GB RAM with an Intel integrated GPU. DO NOT use CUDA-exclusive libraries. 
- **Local AI:** Use CPU-optimized libraries like `faster-whisper` (CTranslate2) or Intel OpenVINO for local speech-to-text.
- **Cloud AI:** Heavy natural language processing and scene selection must be offloaded to external LLM APIs (e.g., Claude/OpenAI) to prevent system freezing.

## Video Output Defaults
- **Format:** 9:16 vertical video (1080x1920 resolution).
- **Layout:** Split-screen. Facecam cropped and centered at the top half; gameplay/main content cropped and centered at the bottom half.
- **Subtitles:** `.ass` (Advanced SubStation Alpha) format for dynamic, word-by-word highlighting. Hardcoded into the final video via FFmpeg.
- **Framerate:** Match the source video framerate (usually 30 or 60 fps).

## Testing & Workflow
- **Test Chunks:** Never process a 4-hour VOD during testing. Always use FFmpeg to extract a 3-minute test sample (`ffmpeg -ss 00:10:00 -t 00:03:00`) before running the full pipeline.
- **File Management:** Save intermediate files (e.g., extracted `.wav`, transcribed `.json`, generated `.ass`) in a dedicated `/temp` folder so the pipeline can be resumed without starting from scratch.
- **Log, don't print:** Use Python's `logging` module instead of `print()`. Log FFmpeg stdout/stderr to files for debugging.

## Anti-Generic Guardrails
- **JSON Strictness:** The LLM scene analysis MUST return highly structured JSON (e.g., via Pydantic or Instructor). Never accept unstructured conversational text like "Here are your clips...".
- **Audio-Visual Synergy:** Do not rely solely on text for scene selection. Incorporate audio peak detection (RMS) to identify loud/excited moments.
- **FFmpeg Subprocesses:** Never use raw `os.system` for FFmpeg. Use `subprocess.run` with proper timeout and error handling, or a dedicated wrapper like `ffmpeg-python`.

## Hard Rules
- Do not introduce heavy C++ compilation dependencies unless absolutely necessary. Stick to pure Python or pre-compiled binaries.
- Do not overwrite the original source video files.
- Do not proceed to the next pipeline step if the previous step's output fails validation (e.g., empty audio file, malformed JSON).
- Do not hallucinate timestamps. The AI analysis must only select timestamps that exist exactly in the Whisper transcription data.