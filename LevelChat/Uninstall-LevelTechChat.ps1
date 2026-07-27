#############################################################################
#  DS Business Hub - Level Tech Chat  :  UNINSTALLER                        #
#  Run in Level.io as SYSTEM to fully remove the watcher and its task.      #
#############################################################################

$ErrorActionPreference = "SilentlyContinue"
function Get-TimeStamp { return "[{0:yyyy-MM-dd HH:mm:ss}]" -f (Get-Date) }
function Info($m) { Write-Host "$(Get-TimeStamp) $m" }

$InstallDir  = Join-Path $env:ProgramData "DSBusinessHub\LevelTechChat"
$TaskName    = "DSBusinessHub-LevelTechChat"

Info "Removing scheduled task '$TaskName'..."
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

# Stop any running watcher (hidden powershell running Watcher.ps1) and any chat windows.
Info "Stopping any running watcher / chat windows..."
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "LevelTechChat\\Watcher\.ps1" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "DSBH-LevelTechChat-Edge" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

if (Test-Path $InstallDir) {
    Info "Deleting $InstallDir ..."
    Remove-Item -Path $InstallDir -Recurse -Force
}

# Clean up the dedicated Edge profile in each user profile if present.
Get-ChildItem "C:\Users" -Directory -EA SilentlyContinue | ForEach-Object {
    $p = Join-Path $_.FullName "AppData\Local\DSBH-LevelTechChat-Edge"
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

Info "DONE. Level Tech Chat removed."
exit 0
