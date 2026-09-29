# Run Mbeu Trace Farm Traceability server
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "Starting Mbeu Trace Farm Traceability server..." -ForegroundColor Green
Write-Host "Open http://localhost:5000 in your browser." -ForegroundColor Cyan
& "$scriptDir\.venv\Scripts\python.exe" "$scriptDir\farm_traceability\app.py"
