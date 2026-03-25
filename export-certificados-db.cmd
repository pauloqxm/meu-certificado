@echo off
REM Chama o PowerShell para passar o token no header (funciona com #, +, & no token).
REM Exemplo:
REM   export-certificados-db.cmd "O_TEU_TOKEN_AQUI"
REM Ou com URL e formato JSON:
REM   export-certificados-db.cmd "TOKEN" "https://meu-certificado.up.railway.app" json

setlocal
set "TOKEN=%~1"
if "%TOKEN%"=="" (
  echo Uso: %~nx0 TOKEN [BaseUrl] [sqlite^|json]
  exit /b 1
)

set "BASE=%~2"
if "%BASE%"=="" set "BASE=https://meu-certificado.up.railway.app"

set "FMT=%~3"
if "%FMT%"=="" set "FMT=sqlite"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0export-certificados-db.ps1" -Token "%TOKEN%" -BaseUrl "%BASE%" -Format "%FMT%"
exit /b %ERRORLEVEL%
