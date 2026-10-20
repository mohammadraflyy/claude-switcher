# Build ClaudeSwitcher.exe (PyInstaller) then installer (Inno Setup).
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$version = "1.0.0"

if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
$py = ".venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt pyinstaller
if ($LASTEXITCODE) { throw "pip install failed" }

& $py -m unittest test_switcher
if ($LASTEXITCODE) { throw "unit tests failed" }

if (-not (Test-Path "assets\icon.ico")) { & $py make_icon.py }

# folder build, no UPX: fewer antivirus false positives. --windowed: tray only, no console.
& $py -m PyInstaller --noconfirm --clean --noupx --windowed `
    --name ClaudeSwitcher --icon assets\icon.ico `
    --hidden-import pystray._win32 `
    tray.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup not found (winget install JRSoftware.InnoSetup). Exe is in dist\ClaudeSwitcher\"
    exit 0
}
& $iscc /Q "/DAppVersion=$version" installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Write-Host "Done: dist\ClaudeSwitcher-Setup-$version.exe" -ForegroundColor Green
