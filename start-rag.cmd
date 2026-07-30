@echo off
cd /d "%~dp0"
echo ===========================================
echo   Wikipedia RAG (ruri-embed)
echo ===========================================
echo.
python webui.py --index index --model qwen3:14b
echo.
echo Press any key to exit...
pause >nul
