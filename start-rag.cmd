@echo off
setlocal
cd /d "%~dp0"

echo ===========================================
echo   Wikipedia RAG (ruri-embed)
echo ===========================================

python webui.py --viewer jsonl --open-browser

if errorlevel 1 pause
endlocal