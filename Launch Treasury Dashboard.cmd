@echo off
cd /d "%~dp0"
python -m streamlit run app.py
if errorlevel 1 (
    echo.
    echo Dashboard exited with an error. Review the output above and README.md setup instructions.
    pause
    exit /b 1
)
