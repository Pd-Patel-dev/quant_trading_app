# Run daily reconciliation worker
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$LogDir = Join-Path $ProjectRoot "storage\logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("reconciliation_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython"
}

& $VenvPython -m workers.daily_reconciliation 2>&1 | Tee-Object -FilePath $LogFile
exit $LASTEXITCODE
