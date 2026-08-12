@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem This file is in scripts\; run all commands from the project root.
pushd "%~dp0.." >nul
if errorlevel 1 goto root_error
set "ROOT=%CD%"
set "VENV_PYTHON=%ROOT%\.venv\Scripts\python.exe"

where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if errorlevel 1 goto python_error
set "PYTHON_CMD=python"
goto python_ready

:use_py
set "PYTHON_CMD=py -3"

:python_ready
if exist "%VENV_PYTHON%" goto venv_ready
echo [first run] Creating virtual environment...
%PYTHON_CMD% -m venv "%ROOT%\.venv"
if errorlevel 1 goto venv_error

:venv_ready
rem requirements hash detection: reinstall when the dependency list changes
set "REQ_HASH_FILE=%ROOT%\.venv\.requirements-hash"
set "CURRENT_HASH="
for /f "usebackq delims=" %%h in (`certutil -hashfile "%ROOT%\requirements.txt" SHA256 ^| findstr /v "hash" ^| findstr /v ":"`) do set "CURRENT_HASH=!CURRENT_HASH!%%h"
set "INSTALLED_HASH="
if exist "%REQ_HASH_FILE%" set /p INSTALLED_HASH=<"%REQ_HASH_FILE%"
if "%CURRENT_HASH%"=="" goto deps_import_check
if "%CURRENT_HASH%"=="%INSTALLED_HASH%" goto deps_import_check
echo [install] Dependency list changed; installing backend dependencies...
"%VENV_PYTHON%" -m pip install -r "%ROOT%\requirements.txt"
if errorlevel 1 goto pip_error
echo %CURRENT_HASH%> "%REQ_HASH_FILE%"
goto deps_ready

:deps_import_check
"%VENV_PYTHON%" -c "import fastapi, uvicorn, argon2" >nul 2>nul
if not errorlevel 1 goto deps_ready
echo [first run] Installing Python dependencies...
"%VENV_PYTHON%" -m pip install -r "%ROOT%\requirements.txt"
if errorlevel 1 goto pip_error
echo %CURRENT_HASH%> "%REQ_HASH_FILE%"

:deps_ready
if exist "%ROOT%\frontend\dist\index.html" goto frontend_ready
goto frontend_error

:frontend_ready
echo [start] Opening http://127.0.0.1:8848
start "Shimo" http://127.0.0.1:8848
"%VENV_PYTHON%" -m app
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%

:root_error
echo [error] Cannot locate the project root.
pause
exit /b 1

:python_error
echo [error] Python 3.10 or newer was not found.
echo Install Python from https://www.python.org/downloads/ and run this file again.
pause
exit /b 1

:venv_error
echo [error] Failed to create the virtual environment.
pause
exit /b 1

:pip_error
echo [error] Failed to install Python dependencies. Check the network and retry.
pause
exit /b 1

:frontend_error
echo [error] frontend\dist\index.html was not found.
echo Build it from the frontend directory with: npm install ^&^& npm run build
pause
exit /b 1
