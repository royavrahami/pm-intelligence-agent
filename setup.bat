@echo off
REM PM Intelligence Agent – Windows Setup Script

echo Setting up PM Intelligence Agent...

REM Create virtual environment
python -m venv .venv
call .venv\Scripts\activate.bat

REM Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

REM Create .env if it doesn't exist
if not exist .env (
    copy .env.example .env
    echo Created .env from .env.example – please edit it and add your OPENAI_API_KEY
)

REM Create required directories
if not exist data mkdir data
if not exist reports mkdir reports
if not exist logs mkdir logs

REM Generate run.bat launcher with PYTHONNOUSERSITE=1 to prevent
REM Windows Store Python stubs from shadowing venv packages
(
    echo @echo off
    echo SET PYTHONNOUSERSITE=1
    echo call "%%~dp0.venv\Scripts\activate.bat"
    echo python main.py %%*
) > run.bat

echo.
echo Setup complete!
echo Next steps:
echo   1. Edit .env and set OPENAI_API_KEY=sk-...
echo   2. Run: run.bat run          (Option A - recommended on Windows)
echo      OR:  python main.py run   (Option B - if venv is already activated)
echo   3. For recurring schedule: run.bat schedule
