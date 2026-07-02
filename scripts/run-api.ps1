param(
    [switch]$LogToFile,
    [switch]$Lan,
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if ($Lan) {
    $HostAddress = "0.0.0.0"
}

$CommandArgs = @(
    "-m", "uvicorn", "steptwin_api.main:app",
    "--app-dir", "src",
    "--host", $HostAddress,
    "--port", $Port
)

if ($LogToFile) {
    $LogDir = Join-Path $ProjectRoot "logs"
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    & "$ProjectRoot\.venv\Scripts\python.exe" @CommandArgs *> (Join-Path $LogDir "api.log")
    exit $LASTEXITCODE
}

& "$ProjectRoot\.venv\Scripts\python.exe" @CommandArgs
