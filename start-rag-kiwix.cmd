@echo off
setlocal
cd /d "%~dp0"

rem ---- Change these three values for your installation. ----
set "KIWIX_SERVE=tools\kiwix\kiwix-serve.exe"
set "KIWIX_ZIM=tools\kiwix\wikipedia_ja_all_nopic_2026-06.zim"
set "KIWIX_BOOK=wikipedia_ja_all_nopic"

python webui.py ^
  --viewer kiwix ^
  --kiwix-executable "%KIWIX_SERVE%" ^
  --kiwix-zim-file "%KIWIX_ZIM%" ^
  --kiwix-zim "%KIWIX_BOOK%" ^
  --kiwix-url http://127.0.0.1:8080 ^
  --open-browser

if errorlevel 1 pause
endlocal
