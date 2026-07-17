# FizMine Panel Installer for Windows

Write-Host ""
Write-Host "  _____ _     __  __ _            " -ForegroundColor Cyan
Write-Host " |  ___(_)___|  \/  (_)_ __   ___ " -ForegroundColor Cyan
Write-Host " | |_  | |_  / |\/| | | '_ \ / _ \" -ForegroundColor Cyan
Write-Host " |  _| | |/ /| |  | | | | | |  __/" -ForegroundColor Cyan
Write-Host " |_|   |_/___|_|  |_|_|_| |_|\___| " -ForegroundColor Cyan
Write-Host "          Panel Installer" -ForegroundColor Cyan
Write-Host ""

# Install path
$defaultPath = "C:\minecraft"
$installDir = Read-Host "Install path [$defaultPath]"
if (-not $installDir) { $installDir = $defaultPath }

# Auth
$authChoice = Read-Host "Enable authentication? (y/n) [n]"
if (-not $authChoice) { $authChoice = "n" }

# Port
$panelPort = Read-Host "Panel port [8080]"
if (-not $panelPort) { $panelPort = "8080" }

Write-Host ""
Write-Host "Installing to: $installDir"
Write-Host "Auth: $authChoice"
Write-Host "Port: $panelPort"
Write-Host ""

# Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue) -and -not (Get-Command python3 -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found. Install from https://www.python.org/downloads/" -ForegroundColor Yellow
    $answer = Read-Host "Open Python download page? (y/n)"
    if ($answer -eq "y") { Start-Process "https://www.python.org/downloads/" }
    exit 1
}

# Check Java
if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
    Write-Host "Java 17+ not found. Install from https://adoptium.net/" -ForegroundColor Yellow
    $answer = Read-Host "Open Java download page? (y/n)"
    if ($answer -eq "y") { Start-Process "https://adoptium.net/temurin/releases/?version=17" }
    exit 1
}

Write-Host "Downloading FizMine Panel..."
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$tempFile = "$env:TEMP\fizmine-panel.zip"
Invoke-WebRequest -Uri "https://github.com/fizyCH/FizMine/releases/download/FizMine_Login_and_Play%21/panel.zip" -OutFile $tempFile

Write-Host "Extracting to $installDir..."
Expand-Archive -Path $tempFile -DestinationPath $installDir -Force
Remove-Item $tempFile

# Write .env
$authToken = ""
if ($authChoice -eq "y" -or $authChoice -eq "Y") {
    $authToken = Read-Host "Set authentication password"
}

$envContent = @"
PANEL_PORT=$panelPort
MC_DIR=$installDir
PANEL_TOKEN=$authToken
"@
Set-Content -Path "$installDir\.env" -Value $envContent

Write-Host ""
Write-Host "  Installation complete!" -ForegroundColor Green
Write-Host "  ======================" -ForegroundColor Green
Write-Host "  cd $installDir"
Write-Host "  python panel.py"
Write-Host "  Panel: http://localhost:$panelPort"
Write-Host ""
