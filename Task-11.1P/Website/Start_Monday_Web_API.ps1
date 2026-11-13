$ErrorActionPreference = 'Stop'
Set-Location 'C:\MondayServer'
$Python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Monday virtual environment not found.' }
Write-Host 'Starting Monday Website API on all interfaces, port 8002.'
Write-Host 'Keep this window open while the Raspberry Pi uses alarms and study check-ins.'
& $Python -m uvicorn monday_web:app --host 0.0.0.0 --port 8002
