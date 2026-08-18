@echo off
REM Double-click launcher for Windows.
cd /d "%~dp0"

python -c "import streamlit" 2>nul
if errorlevel 1 (
    echo Installing required packages ^(first run only^)...
    pip install -r requirements.txt
)

streamlit run app.py
pause
