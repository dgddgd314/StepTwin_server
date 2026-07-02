param(
    [switch]$LogToFile
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$CommandArgs = @(
    "-m", "uvicorn", "steptwin_api.main:app",
    "--app-dir", "src",
    "--host", "127.0.0.1",
    "--port", "8000"
)

if ($LogToFile) {
    $LogDir = Join-Path $ProjectRoot "logs"
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    & "$ProjectRoot\.venv\Scripts\python.exe" @CommandArgs *> (Join-Path $LogDir "api.log")
    exit $LASTEXITCODE
}

& "$ProjectRoot\.venv\Scripts\python.exe" @CommandArgs
