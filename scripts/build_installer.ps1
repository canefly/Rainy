# ==============================================================================
# Clickables Tools Native - Inno Setup Installer Builder
# ==============================================================================
# Automates compiling the Inno Setup installer from dist_nuitka\main.dist
# ==============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Set-Location $ProjectRoot
Write-Host ">>> Starting Clickables Setup Installer Build..." -ForegroundColor Cyan

# 1. Verify that the standalone distribution exists
$ExePath = Join-Path $ProjectRoot "dist_nuitka\main.dist\ClickablesParser.exe"
if (-not (Test-Path $ExePath)) {
    Write-Host ">>> Standalone distribution not found. Running Nuitka build first..." -ForegroundColor Yellow
    & "$ScriptDir\build_nuitka.ps1"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Nuitka compilation failed. Aborting installer creation."
    }
}

# 2. Locate ISCC.exe (Inno Setup Compiler)
$IsccCmd = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
$IsccPath = ""

if ($IsccCmd) {
    $IsccPath = $IsccCmd.Source
} else {
    $CommonPaths = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:LocalAppData}\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($p in $CommonPaths) {
        if (Test-Path $p) {
            $IsccPath = $p
            break
        }
    }
}

if (-not $IsccPath) {
    Write-Host "[WARNING] Inno Setup Compiler (ISCC.exe) was not found on your system." -ForegroundColor Yellow
    Write-Host "You can install it automatically using winget:" -ForegroundColor White
    Write-Host "  winget install JRSoftware.InnoSetup -e" -ForegroundColor Green
    Write-Host "Or download it from: https://jrsoftware.org/isdl.php" -ForegroundColor Cyan
    exit 1
}

Write-Host ">>> Found Inno Setup Compiler at: $IsccPath" -ForegroundColor Green

# 3. Create dist output directory
$DistDir = Join-Path $ProjectRoot "dist"
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
}

# 4. Compile Installer
$IssPath = Join-Path $ProjectRoot "installer\setup.iss"
Write-Host ">>> Compiling installer via $IssPath..." -ForegroundColor Cyan
& "$IsccPath" "$IssPath"

if ($LASTEXITCODE -eq 0) {
    Write-Host "=================================================================" -ForegroundColor Green
    Write-Host " SUCCESS: Inno Setup installer compiled successfully!" -ForegroundColor Green
    Write-Host " Directory: $DistDir" -ForegroundColor White
    Write-Host "=================================================================" -ForegroundColor Green
} else {
    Write-Error "ISCC.exe failed with exit code $LASTEXITCODE"
}
