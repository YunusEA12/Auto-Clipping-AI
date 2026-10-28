param(
    [ValidateSet('dashboard', 'supervisor', 'check', 'test', 'audit')]
    [string]$Mode = 'dashboard'
)

$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'uv fehlt. Installieren: https://docs.astral.sh/uv/getting-started/installation/'
    }
    & uv sync --locked --group dev
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    switch ($Mode) {
        'dashboard' { & uv run --locked streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false }
        'supervisor' { & uv run --locked python health_check.py; if ($LASTEXITCODE -eq 0) { & uv run --locked python process_supervisor.py } }
        'check' { & uv run --locked python health_check.py }
        'test' { & uv run --locked pytest -q }
        'audit' { & uv run --locked python clip_quality_audit.py --watch }
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
