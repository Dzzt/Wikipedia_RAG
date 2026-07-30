@echo off
setlocal

cd /d "%~dp0"

set "RAG_URL=http://127.0.0.1:8765"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
set "CHROME_PROFILE=%TEMP%\Wikipedia_RAG_Chrome"

if not exist "%CHROME%" (
    set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
)

if not exist "%CHROME%" (
    echo ChromeÇ™å©Ç¬Ç©ÇËÇ‹ÇπÇÒÅB
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$root = (Get-Location).Path;" ^
  "$server = Start-Process python -ArgumentList 'webui.py' -WorkingDirectory $root -PassThru -NoNewWindow;" ^
  "try {" ^
  "  $ready = $false;" ^
  "  for ($i = 0; $i -lt 60; $i++) {" ^
  "    try {" ^
  "      Invoke-WebRequest '%RAG_URL%/api/health' -UseBasicParsing -TimeoutSec 1 | Out-Null;" ^
  "      $ready = $true;" ^
  "      break;" ^
  "    } catch {" ^
  "      Start-Sleep -Milliseconds 500;" ^
  "    }" ^
  "  }" ^
  "  if (-not $ready) { throw 'Web UIÇãNìÆÇ≈Ç´Ç‹ÇπÇÒÇ≈ÇµÇΩÅB'; }" ^
  "  $chrome = Start-Process '%CHROME%' -ArgumentList '--app=%RAG_URL%','--user-data-dir=%CHROME_PROFILE%','--no-first-run' -PassThru;" ^
  "  Wait-Process -Id $chrome.Id;" ^
  "} finally {" ^
  "  if ($server -and -not $server.HasExited) {" ^
  "    Stop-Process -Id $server.Id -Force;" ^
  "  }" ^
  "}"

endlocal
