@echo off
rem break-forecast launcher for Windows.
rem   run.bat                      open the web UI (replay recorded runs, start live runs)
rem   run.bat run <repo-or-url>    run the whole pipeline from the command line
rem   run.bat <any bf command>     e.g. run.bat scan https://github.com/rishrek/LinkWatch
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo break-forecast needs uv. Install it with:
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  echo then open a new terminal and run this file again.
  exit /b 1
)

if not exist ".venv\Scripts\bf.exe" (
  echo [setup] creating .venv and installing break-forecast...
  uv venv .venv --python 3.12 || exit /b 1
  uv pip install --python .venv\Scripts\python.exe -e ".[dev]" || exit /b 1
  echo [setup] installing the target interpreters Python 3.12 and 3.13...
  uv python install 3.12 3.13 || exit /b 1
)

if not exist ".env" (
  echo [note] no .env found: copy .env.example to .env and add your Gemma API key to use the AI steps.
  echo        Without it the AI steps fall back to a local Ollama model, or use --no-ai.
)

rem Give the UI a recorded run to replay on a fresh clone.
if not exist "runs\20261015-151158" (
  if exist "examples\runs\20261015-151158" xcopy /e /i /q "examples\runs\20261015-151158" "runs\20261015-151158" >nul
)

if "%~1"=="" (
  echo [bf] opening the web UI at http://127.0.0.1:8770/  ^(Ctrl+C to stop^)
  ".venv\Scripts\bf.exe" serve --open --runs-dir runs --project-dir .
) else (
  ".venv\Scripts\bf.exe" %*
)
