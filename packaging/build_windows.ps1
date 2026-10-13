param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Build the Windows executable on Windows.' }
$project = Split-Path $PSScriptRoot -Parent
Push-Location $project
try {
    & $Python -m PyInstaller --noconfirm --clean webcam_capture.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    $bundle = Join-Path $project 'dist\WebcamCapture'
    Copy-Item -LiteralPath "$PSScriptRoot\create_shortcut.ps1" -Destination $bundle
    Copy-Item -LiteralPath "$PSScriptRoot\Create-Desktop-Shortcut.cmd" -Destination $bundle
    Copy-Item -LiteralPath "$PSScriptRoot\README-Windows.txt" -Destination "$bundle\README.txt"
    Compress-Archive -Path $bundle -DestinationPath 'dist\WebcamCapture-Windows-x64.zip' -Force
    Write-Host 'Built dist\WebcamCapture-Windows-x64.zip'
} finally { Pop-Location }
