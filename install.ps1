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
    Write-Host "Python not found. Installing..." -ForegroundColor Yellow
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget) {
        winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    } else {
        Write-Host "Please install Python from https://www.python.org/downloads/" -ForegroundColor Yellow
        Start-Process "https://www.python.org/downloads/"
        exit 1
    }
}

# Check Java - install if missing
if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
    Write-Host "Java not found. Installing Java 17..." -ForegroundColor Yellow
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget) {
        winget install EclipseAdoptium.Temurin.17.JDK --silent --accept-package-agreements --accept-source-agreements
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    } else {
        Write-Host "Downloading Java 17 manually..." -ForegroundColor Yellow
        $javaUrl = "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.12%2B7/OpenJDK17U-jdk_x64_windows_hotspot_17.0.12_7.msi"
        $javaInstaller = "$env:TEMP\java17.msi"
        try {
            Invoke-WebRequest -Uri $javaUrl -OutFile $javaInstaller -UseBasicParsing
            Start-Process msiexec.exe -ArgumentList "/i `"$javaInstaller`" /quiet /norestart ADDLOCAL=FeatureMain,FeatureEnvironment,FeatureJarFileRunWith,FeatureJavaHome" -Wait
            Remove-Item $javaInstaller -ErrorAction SilentlyContinue
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        } catch {
            Write-Host "Failed to install Java. Download manually from: https://adoptium.net/" -ForegroundColor Red
            Start-Process "https://adoptium.net/temurin/releases/?version=17"
            exit 1
        }
    }
}

# Verify Java
if (Get-Command java -ErrorAction SilentlyContinue) {
    java -version 2>&1 | Select-Object -First 1 | ForEach-Object { Write-Host "  $_" -ForegroundColor Green }
} else {
    Write-Host "Java installation failed. Please install manually." -ForegroundColor Red
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
