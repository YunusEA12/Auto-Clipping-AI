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
        'dashboard' { & uv run --locked autoclip dashboard --server.port 8501 --browser.gatherUsageStats false }
        'supervisor' { & uv run --locked autoclip check; if ($LASTEXITCODE -eq 0) { & uv run --locked autoclip supervisor } }
        'check' { & uv run --locked autoclip check }
        'test' { & uv run --locked pytest -q }
        'audit' { & uv run --locked autoclip audit --watch }
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
