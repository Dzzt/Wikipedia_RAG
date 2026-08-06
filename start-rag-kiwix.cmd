@echo off
setlocal
cd /d "%~dp0"

echo ===========================================
echo   Wikipedia RAG (ruri-embed)
echo     -- with kiwix wikipedia viewer.
echo ===========================================

python webui.py --viewer kiwix ^
  --kiwix-executable .\tools\kiwix\kiwix-serve.exe ^
  --kiwix-zim-file .\tools\kiwix\wikipedia_ja_all_nopic_2026-06.zim ^
  --kiwix-zim "wikipedia_ja_all_nopic_2026-06" ^
  --open-browser

if errorlevel 1 pause
endlocal
