$ErrorActionPreference = 'Stop'
$executable = Join-Path $PSScriptRoot 'WebcamCapture.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw 'Extract the complete package before creating the shortcut.'
}
$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if ([string]::IsNullOrWhiteSpace($desktop)) { throw 'Your desktop directory could not be found.' }
New-Item -ItemType Directory -Path $desktop -Force | Out-Null
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop 'Webcam Capture.lnk'))
$shortcut.TargetPath = $executable
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Arguments = ''
$shortcut.IconLocation = "$executable,0"
$shortcut.Description = 'Capture photos and export a labeled image set.'
$shortcut.Save()
Write-Host 'Created Webcam Capture on your desktop.'
