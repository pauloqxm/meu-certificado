# Servidor em http://127.0.0.1:8000 (Python do Anaconda, sem .venv)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$python = $env:PYTHON
if (-not $python) {
  $python = "C:\Users\paulo.ferreira\AppData\Local\anaconda3\python.exe"
}

if (-not (Test-Path $python)) {
  Write-Error "Python não encontrado: $python"
}

& $python -m uvicorn @(
  "app.main:app",
  "--reload",
  "--host", "127.0.0.1",
  "--port", "8000"
)
