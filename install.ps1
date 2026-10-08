# One-command install for this machine. Uses the project virtualenv, not a second package manager.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    python -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (Test-Path web\package.json) {
    npm install --prefix web
}
if (Test-Path diagrams\node_worker\package.json) {
    npm install --prefix diagrams\node_worker
}
.\.venv\Scripts\python.exe -m spark_agent ready
