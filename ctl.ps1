$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $ScriptDir ".env"

# Load .env
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
    Write-Host "  _____ _     __  __ _            " -ForegroundColor Cyan
    Write-Host " |  ___(_)___|  \/  (_)_ __   ___ " -ForegroundColor Cyan
    Write-Host " | |_  | |_  / |\/| | | '_ \ / _ \" -ForegroundColor Cyan
    Write-Host " |  _| | |/ /| |  | | | | | |  __/" -ForegroundColor Cyan
    Write-Host " |_|   |_/___|_|  |_|_|_| |_|\___| " -ForegroundColor Cyan
    Write-Host "          Control Panel" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  1) Change port"
    Write-Host "  2) Delete panel"
    Write-Host "  3) Java version"
    Write-Host "  4) Exit"
    Write-Host ""
}

function Start-Panel {
    $proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*panel.py*" }
    if ($proc) {
        Write-Host "Panel is already running"
        return
    }
    Start-Process python -ArgumentList "panel.py" -WorkingDirectory $ScriptDir -WindowStyle Minimized
    Start-Sleep 1
    Write-Host "Panel started on http://0.0.0.0:$PanelPort"
}

function Stop-Panel {
    $proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*panel.py*" }
    if ($proc) {
        Stop-Process -Name python* -Force -ErrorAction SilentlyContinue
        Write-Host "Panel stopped"
    } else {
        Write-Host "Panel is not running"
    }
}

function Restart-Panel {
    Stop-Panel
    Start-Sleep 1
    Start-Panel
}

function Change-Port {
    Write-Host ""
    Write-Host "Current port: $PanelPort"
    $newPort = Read-Host "New port"
    if ($newPort) {
        $content = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
        if ($content -match "PANEL_PORT=") {
            $content = $content -replace "PANEL_PORT=.*", "PANEL_PORT=$newPort"
        } else {
            $content += "`nPANEL_PORT=$newPort"
        }
        Set-Content -Path $EnvFile -Value $content
        Write-Host "Port changed to $newPort"
        $restart = Read-Host "Restart panel now? (y/n) [y]"
        if (-not $restart -or $restart -eq "y" -or $restart -eq "Y") {
            Restart-Panel
        }
    }
}

function Delete-Panel {
    Write-Host ""
    Write-Host "WARNING: This will delete the entire panel directory!" -ForegroundColor Yellow
    Write-Host "Path: $ScriptDir"
    $confirm = Read-Host "Are you sure? (y/n) [n]"
    if ($confirm -eq "y" -or $confirm -eq "Y") {
        Stop-Panel
        Remove-Item -Path $ScriptDir -Recurse -Force
        Write-Host "Panel deleted."
        exit
    } else {
        Write-Host "Cancelled."
    }
}

function Check-Java {
    Write-Host ""
    try {
        java -version 2>&1 | Select-Object -First 1
    } catch {
        Write-Host "Java not found!" -ForegroundColor Yellow
    }
}

# If argument passed
if ($args.Count -gt 0) {
    switch ($args[0]) {
        "start"   { Start-Panel }
        "stop"    { Stop-Panel }
        "restart" { Restart-Panel }
        "status"  {
            $proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*panel.py*" }
            if ($proc) { Write-Host "Panel is running (PID: $($proc.Id))" }
            else { Write-Host "Panel is not running" }
        }
        "log"     {
            $logPath = Join-Path $ScriptDir "..\logs\latest.log"
            if (Test-Path $logPath) { Get-Content $logPath -Tail 50 }
            else { Write-Host "No log file found" }
        }
        default   { Write-Host "Usage: .\ctl.ps1 {start|stop|restart|status|log}" }
    }
    exit
}

# Interactive menu
while ($true) {
    Show-Menu
    $choice = Read-Host "Select [1-4]"
    switch ($choice) {
        "1" { Change-Port }
        "2" { Delete-Panel }
        "3" { Check-Java }
        "4" { Write-Host "Bye!"; exit }
        default { Write-Host "Invalid choice" }
    }
    Write-Host ""
    Read-Host "Press Enter to continue"
}
