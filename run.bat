@echo off
SET PYTHONNOUSERSITE=1
call "%~dp0.venv\Scripts\activate.bat"
python main.py %*
