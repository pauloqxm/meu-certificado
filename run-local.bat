@echo off
setlocal
cd /d "%~dp0"
call "%~dp0local-env.cmd"
if not exist "%PYTHON%" (
  echo Ajuste PYTHON em local-env.cmd
  exit /b 1
)
"%PYTHON%" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
endlocal
