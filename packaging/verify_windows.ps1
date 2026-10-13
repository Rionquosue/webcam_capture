$ErrorActionPreference = 'Stop'
$bundle = (Resolve-Path (Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\WebcamCapture')).Path
$executable = Join-Path $bundle 'WebcamCapture.exe'
$env:QT_QPA_PLATFORM = 'offscreen'
$process = Start-Process -FilePath $executable -ArgumentList '--smoke-test' -PassThru
if (-not $process.WaitForExit(30000)) {
    Stop-Process -Id $process.Id -Force
    throw 'Packaged application launch timed out.'
}
if ($process.ExitCode -ne 0) { throw "Packaged application failed: $($process.ExitCode)" }
Add-Type -AssemblyName System.Drawing
$icon = [Drawing.Icon]::ExtractAssociatedIcon($executable)
if ($null -eq $icon) { throw 'The executable has no readable icon.' }
$icon.Dispose()
& (Join-Path $bundle 'create_shortcut.ps1')
& (Join-Path $bundle 'create_shortcut.ps1')
$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
$path = Join-Path $desktop 'Webcam Capture.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($path)
if ($shortcut.TargetPath -ne $executable) { throw 'Shortcut target does not match the packaged executable.' }
if ($shortcut.WorkingDirectory -ne $bundle) { throw 'Shortcut working directory is incorrect.' }
if ($shortcut.IconLocation -ne "$executable,0") { throw 'Shortcut icon is incorrect.' }
Remove-Item -LiteralPath $path
Write-Host 'Packaged launch, executable icon, and desktop shortcut verified.'
