@echo off
setlocal
cd /d "%~dp0"
call "%~dp0local-env.cmd"
if not exist "%PYTHON%" (
  echo Ajuste PYTHON em local-env.cmd
  exit /b 1
)
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
echo.
echo Dependencias instaladas no Anaconda. Inicie com: run-local.bat
endlocal
