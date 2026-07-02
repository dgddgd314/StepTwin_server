param(
    [int]$Port = 8000
)

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
        "-File", "`"$PSCommandPath`"",
        "-Port", $Port
    )
    exit
}

$ExistingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue

if ($ExistingRule) {
    Set-NetFirewallRule -DisplayName $RuleName `
        -Enabled True `
        -Direction Inbound `
        -Action Allow `
        -Profile Public,Private

    Set-NetFirewallPortFilter -AssociatedNetFirewallRule $ExistingRule `
        -Protocol TCP `
        -LocalPort $Port

    Set-NetFirewallAddressFilter -AssociatedNetFirewallRule $ExistingRule `
        -RemoteAddress LocalSubnet
} else {
    New-NetFirewallRule `
        -DisplayName $RuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Public,Private `
        -RemoteAddress LocalSubnet `
        -Description "Allow Steptwin FastAPI dev server from local subnet only."
}

Write-Host "Allowed TCP port $Port for local subnet only."
