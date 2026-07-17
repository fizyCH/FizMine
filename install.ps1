# FizMine Panel Installer for Windows
param(
    [string]$InstallDir = "C:\minecraft"
)

Write-Host ""
Write-Host "====================================" -ForegroundColor Cyan
Write-Host "      FizMine Panel Installer" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue) -and -not (Get-Command python3 -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found. Install from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "Make sure to check 'Add Python to PATH' during installation."
    Write-Host ""
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
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$tempFile = "$env:TEMP\fizmine-panel.zip"
Invoke-WebRequest -Uri "https://github.com/fizyCH/FizMine/releases/download/FizMine_Login_and_Play!/panel.zip" -OutFile $tempFile

Write-Host "Extracting to $InstallDir..."
Expand-Archive -Path $tempFile -DestinationPath $InstallDir -Force
Remove-Item $tempFile

Write-Host ""
Write-Host "====================================" -ForegroundColor Green
Write-Host "     Installation complete!" -ForegroundColor Green
Write-Host "====================================" -ForegroundColor Green
Write-Host ""
Write-Host "  cd $InstallDir"
Write-Host "  python panel.py"
Write-Host "  Panel: http://localhost:8080"
Write-Host ""
