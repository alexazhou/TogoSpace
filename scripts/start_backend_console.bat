@echo off
setlocal

set "REPO_ROOT=%~dp0.."
set "SRC_DIR=%REPO_ROOT%\src"
set "PYTHON_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

start "TogoSpace Backend" cmd /k "cd /d "%SRC_DIR%" && "%PYTHON_EXE%" backend_main.py %*"
