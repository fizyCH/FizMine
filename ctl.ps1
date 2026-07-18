$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $ScriptDir ".env"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match "^([^#=]+)=(.*)$") {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
        }
    }
}

$McDir = if ($env:MC_DIR) { $env:MC_DIR } else { $ScriptDir }
$PanelPort = if ($env:PANEL_PORT) { $env:PANEL_PORT } else { "8080" }

function Show-Menu {
    Clear-Host
    Write-Host ""
    Write-Host "  +----------------------------------------+"
    Write-Host "  |                                        |"
    Write-Host "  | _____ _     __  __ _                   |"
    Write-Host "  | |  ___(_)___|  \/  (_)_ __   ___      |"
    Write-Host "  | | |_  | |_  / |\/| | | '_ \ / _ \     |"
    Write-Host "  | |  _| | |/ /| |  | | | | | |  __/     |"
    Write-Host "  | |_|   |_/___|_|  |_|_|_| |_|\___|     |"
    Write-Host "  |                                        |"
    Write-Host "  |          Control Panel v2.0            |"
    Write-Host "  +----------------------------------------+"
    Write-Host "  |                                        |"
    Write-Host "  |   1) Start panel                       |"
    Write-Host "  |   2) Stop panel                        |"
    Write-Host "  |   3) Restart panel                     |"
    Write-Host "  |   4) Panel status                      |"
    Write-Host "  |   5) Change port                       |"
    Write-Host "  |   6) Java version                      |"
    Write-Host "  |   7) Delete panel                      |"
    Write-Host "  |   8) Exit                              |"
    Write-Host "  |                                        |"
    Write-Host "  +----------------------------------------+"
    Write-Host ""
}

function Start-Panel {
    $proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*panel.py*" }
    if ($proc) {
        Write-Host "  Panel is already running"
        return
    }
    Start-Process python -ArgumentList "panel.py" -WorkingDirectory $ScriptDir -WindowStyle Minimized
    Start-Sleep 1
    Write-Host "  Panel started -> http://0.0.0.0:$PanelPort"
}

function Stop-Panel {
    $proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*panel.py*" }
    if ($proc) {
        Stop-Process -Name python* -Force -ErrorAction SilentlyContinue
        Write-Host "  Panel stopped"
    } else {
        Write-Host "  Panel is not running"
    }
}

function Restart-Panel {
    Stop-Panel
    Start-Sleep 1
    Start-Panel
}

function Status-Panel {
    $proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*panel.py*" }
    if ($proc) {
        Write-Host "  Running (PID: $($proc.Id))"
    } else {
        Write-Host "  Stopped"
    }
}

function Change-Port {
    Write-Host ""
    Write-Host "  Port Settings"
    Write-Host "  -------------"
    Write-Host "  Current: $PanelPort"
    $newPort = Read-Host "  New port"
    if ($newPort) {
        $content = if (Test-Path $EnvFile) { Get-Content $EnvFile -Raw } else { "" }
        if ($content -match "PANEL_PORT=") {
            $content = $content -replace "PANEL_PORT=.*", "PANEL_PORT=$newPort"
        } else {
            $content += "`nPANEL_PORT=$newPort"
        }
        Set-Content -Path $EnvFile -Value $content
        Write-Host "  Done! Port changed to $newPort"
        $restart = Read-Host "  Restart panel? (y/n) [y]"
        if (-not $restart -or $restart -eq "y" -or $restart -eq "Y") {
            Restart-Panel
        }
    }
}

function Delete-Panel {
    Write-Host ""
    Write-Host "  +-----------------------------------+"
    Write-Host "  |   WARNING: Delete all files?      |"
    Write-Host "  +-----------------------------------+"
    Write-Host "  Path: $ScriptDir"
    Write-Host ""
    $confirm = Read-Host "  Type 'DELETE' to confirm"
    if ($confirm -eq "DELETE") {
        Stop-Panel
        Remove-Item -Path $ScriptDir -Recurse -Force
        Write-Host "  Panel deleted."
        exit
    } else {
        Write-Host "  Cancelled."
    }
}

function Check-Java {
    Write-Host ""
    Write-Host "  Java"
    Write-Host "  ----"
    try {
        java -version 2>&1 | Select-Object -First 1 | ForEach-Object { Write-Host "  $_" }
        Write-Host "  Java found"
    } catch {
        Write-Host "  Java not found!"
    }
}

if ($args.Count -gt 0) {
    switch ($args[0]) {
        "start"   { Start-Panel }
        "stop"    { Stop-Panel }
        "restart" { Restart-Panel }
        "status"  { Status-Panel }
        "log"     {
            $logPath = Join-Path $ScriptDir "..\logs\latest.log"
            if (Test-Path $logPath) { Get-Content $logPath -Tail 50 }
            else { Write-Host "  No log file found" }
        }
        default   { Write-Host "Usage: .\ctl.ps1 {start|stop|restart|status|log}" }
    }
    exit
}

while ($true) {
    Show-Menu
    $choice = Read-Host "  Select [1-8]"
    switch ($choice) {
        "1" { Start-Panel }
        "2" { Stop-Panel }
        "3" { Restart-Panel }
        "4" { Status-Panel }
        "5" { Change-Port }
        "6" { Check-Java }
        "7" { Delete-Panel }
        "8" { Write-Host "  Bye!"; exit }
        default { Write-Host "  Invalid choice" }
    }
    Write-Host ""
    Read-Host "  Press Enter"
}
