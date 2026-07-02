$ErrorActionPreference = "Stop"
$RuleName = "Steptwin FastAPI Dev Server"

function Test-IsAdministrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    return $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )
    exit
}

Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
Write-Host "Removed firewall rule: $RuleName"
