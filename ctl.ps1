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
    Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor DarkCyan
    Write-Host "  ║  _____ _     __  __ _                ║" -ForegroundColor DarkCyan
    Write-Host "  ║ |  ___(_)___|  \/  (_)_ __   ___    ║" -ForegroundColor DarkCyan
    Write-Host "  ║ | |_  | |_  / |\/| | | '_ \ / _ \   ║" -ForegroundColor DarkCyan
    Write-Host "  ║ |  _| | |/ /| |  | | | | | |  __/   ║" -ForegroundColor DarkCyan
    Write-Host "  ║ |_|   |_/___|_|  |_|_|_| |_|\___|   ║" -ForegroundColor DarkCyan
    Write-Host "  ║         Control Panel v2.0           ║" -ForegroundColor DarkCyan
    Write-Host "  ╠══════════════════════════════════════╣" -ForegroundColor DarkCyan
    Write-Host "  ║                                      ║" -ForegroundColor DarkCyan
    Write-Host "  ║   " -ForegroundColor DarkCyan -NoNewline
    Write-Host "1)" -ForegroundColor Green -NoNewline
    Write-Host " Change port                     ║" -ForegroundColor DarkCyan
    Write-Host "  ║   " -ForegroundColor DarkCyan -NoNewline
    Write-Host "2)" -ForegroundColor Red -NoNewline
    Write-Host " Delete panel                    ║" -ForegroundColor DarkCyan
    Write-Host "  ║   " -ForegroundColor DarkCyan -NoNewline
    Write-Host "3)" -ForegroundColor Yellow -NoNewline
    Write-Host " Java version                    ║" -ForegroundColor DarkCyan
    Write-Host "  ║   " -ForegroundColor DarkCyan -NoNewline
    Write-Host "4)" -ForegroundColor Gray -NoNewline
    Write-Host " Exit                            ║" -ForegroundColor DarkCyan
    Write-Host "  ║                                      ║" -ForegroundColor DarkCyan
    Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor DarkCyan
    Write-Host ""
}

function Start-Panel {
    $proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*panel.py*" }
    if ($proc) {
        Write-Host "  " -NoNewline; Write-Host "⚠ Panel is already running" -ForegroundColor Yellow
        return
    }
    Start-Process python -ArgumentList "panel.py" -WorkingDirectory $ScriptDir -WindowStyle Minimized
    Start-Sleep 1
    Write-Host "  " -NoNewline; Write-Host "✓ Panel started" -ForegroundColor Green -NoNewline
    Write-Host " → http://0.0.0.0:$PanelPort"
}

function Stop-Panel {
    $proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*panel.py*" }
    if ($proc) {
        Stop-Process -Name python* -Force -ErrorAction SilentlyContinue
        Write-Host "  " -NoNewline; Write-Host "✓ Panel stopped" -ForegroundColor Green
    } else {
        Write-Host "  " -NoNewline; Write-Host "⚠ Panel is not running" -ForegroundColor Yellow
    }
}

function Restart-Panel {
    Stop-Panel
    Start-Sleep 1
    Start-Panel
}

function Change-Port {
    Write-Host ""
    Write-Host "  " -NoNewline; Write-Host "Port Settings" -ForegroundColor Cyan
    Write-Host "  " -NoNewline; Write-Host "─────────────" -ForegroundColor DarkGray
    Write-Host "  Current: " -NoNewline; Write-Host "$PanelPort" -ForegroundColor White -NoNewline
    Write-Host ""
    $newPort = Read-Host "  New port"
    if ($newPort) {
        $content = if (Test-Path $EnvFile) { Get-Content $EnvFile -Raw } else { "" }
        if ($content -match "PANEL_PORT=") {
            $content = $content -replace "PANEL_PORT=.*", "PANEL_PORT=$newPort"
        } else {
            $content += "`nPANEL_PORT=$newPort"
        }
        Set-Content -Path $EnvFile -Value $content
        Write-Host "  " -NoNewline; Write-Host "✓ Port changed to $newPort" -ForegroundColor Green
        $restart = Read-Host "  Restart panel? (y/n) [y]"
        if (-not $restart -or $restart -eq "y" -or $restart -eq "Y") {
            Restart-Panel
        }
    }
}

function Delete-Panel {
    Write-Host ""
    Write-Host "  ╔═══════════════════════════════════╗" -ForegroundColor Red
    Write-Host "  ║   ⚠  WARNING: Delete all files?  ║" -ForegroundColor Red
    Write-Host "  ╚═══════════════════════════════════╝" -ForegroundColor Red
    Write-Host "  " -NoNewline; Write-Host "Path: $ScriptDir" -ForegroundColor DarkGray
    Write-Host ""
    $confirm = Read-Host "  Type 'DELETE' to confirm"
    if ($confirm -eq "DELETE") {
        Stop-Panel
        Remove-Item -Path $ScriptDir -Recurse -Force
        Write-Host "  " -NoNewline; Write-Host "Panel deleted." -ForegroundColor Red
        exit
    } else {
        Write-Host "  " -NoNewline; Write-Host "Cancelled." -ForegroundColor Green
    }
}

function Check-Java {
    Write-Host ""
    Write-Host "  " -NoNewline; Write-Host "Java" -ForegroundColor Cyan
    Write-Host "  " -NoNewline; Write-Host "────" -ForegroundColor DarkGray
    try {
        java -version 2>&1 | Select-Object -First 1 | ForEach-Object { Write-Host "  $_" }
        Write-Host "  " -NoNewline; Write-Host "✓ Java found" -ForegroundColor Green
    } catch {
        Write-Host "  " -NoNewline; Write-Host "✗ Java not found!" -ForegroundColor Red
    }
}

if ($args.Count -gt 0) {
    switch ($args[0]) {
        "start"   { Start-Panel }
        "stop"    { Stop-Panel }
        "restart" { Restart-Panel }
        "status"  {
            $proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*panel.py*" }
            if ($proc) {
                Write-Host "  " -NoNewline; Write-Host "● Running" -ForegroundColor Green -NoNewline
                Write-Host " (PID: $($proc.Id))"
            } else {
                Write-Host "  " -NoNewline; Write-Host "● Stopped" -ForegroundColor Red
            }
        }
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
    $choice = Read-Host "  Select [1-4]"
    switch ($choice) {
        "1" { Change-Port }
        "2" { Delete-Panel }
        "3" { Check-Java }
        "4" { Write-Host "`n  $($PSStyle.Foreground.BrightBlack)Bye!$($PSStyle.Reset)"; exit }
        default { Write-Host "  " -NoNewline; Write-Host "Invalid choice" -ForegroundColor Red }
    }
    Write-Host ""
    Read-Host "  Press Enter"
}
