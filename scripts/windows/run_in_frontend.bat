@echo off
rem Windows foreground debug script: keep this window open; closing it terminates the backend process.
setlocal

set "REPO_ROOT=%~dp0..\.."
set "SRC_DIR=%REPO_ROOT%\src"
set "PYTHON_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python3"
)

pushd "%SRC_DIR%" || exit /b 1
echo 请保持窗口在前台，关闭窗口将退出后端
"%PYTHON_EXE%" backend_main.py %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%