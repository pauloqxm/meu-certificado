# Instala dependências no Python do Anaconda (sem virtualenv).
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$python = $env:PYTHON
if (-not $python) {
  $python = "C:\Users\paulo.ferreira\AppData\Local\anaconda3\python.exe"
}

if (-not (Test-Path $python)) {
  Write-Error "Python não encontrado: $python`nDefina `$env:PYTHON ou edite setup-local.ps1 / local-env.cmd"
}

& $python -m pip install -r "requirements.txt"
exit $LASTEXITCODE
