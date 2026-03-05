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

echo.
echo Setup complete!
echo Next steps:
echo   1. Edit .env and set OPENAI_API_KEY=sk-...
echo   2. Run: python main.py run
echo   3. For recurring schedule: python main.py schedule
