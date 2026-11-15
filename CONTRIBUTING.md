# Development

Use Python 3.12, FFmpeg/FFprobe and `uv`. Read `AGENTS.md` and `claude.md` first.

```powershell
uv sync --locked --group dev
uv run --locked playwright install chromium
uv run --locked autoclip --help
```

Create `.env` from `.env.example`; do not commit credentials. Start a fresh private
configuration from `config/streamers.example.json`. For an existing checkout, stop its
services and run the migration preview before `autoclip migrate-runtime --apply`.

Before completing a change:

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check src tests scripts app.py conftest.py
uv run --locked python -m scripts.check_repository
uv run --locked pytest -q --cov=autoclip --cov-report=term
uv build --wheel
uv run --locked python -m scripts.check_wheel
```

Tests use a separate application home. Publishing, notifications and paid API calls need
explicit fakes; do not remove the safety fixtures. AppTest covers Streamlit behaviour.
The real-media test renders generated footage with ASS and audio using CPU FFmpeg and
checks that the source bytes were preserved. GPU-only dependencies are out of scope.

Update dependencies through `uv lock`, then regenerate the compatibility export:

```powershell
uv export --locked --no-dev --no-hashes --no-emit-project --format requirements-txt --output-file requirements.txt
```

The build and the Windows/Linux CI use the lockfile. Build tests run the wheel's own
contents from outside the checkout, checking packaged resources and excluding runtime data.
