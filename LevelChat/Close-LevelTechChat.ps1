#############################################################################
#  DS Business Hub - Level Tech Chat  :  CLOSE CHAT (tech-initiated)        #
#                                                                           #
#  Run from Level.io (as SYSTEM) when you're finished, to close the chat    #
#  window that Open-LevelTechChat.ps1 opened. Only the tagged support-chat  #
#  window is closed - the user's normal browsing is untouched.              #
#                                                    Ver 1.0                #
#############################################################################

$ErrorActionPreference = "SilentlyContinue"
function Get-TimeStamp { return "[{0:yyyy-MM-dd HH:mm:ss}]" -f (Get-Date) }
function Info($m) { Write-Host "$(Get-TimeStamp) $m" }

# Must match $ProfileTag in Open-LevelTechChat.ps1.
$ProfileTag = "DSBH-LevelTechChat-Edge"

$procs = Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($ProfileTag) }

if (-not $procs) {
    Info "No support-chat window found - nothing to close."
    exit 0
}

$count = 0
foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force
    $count++
}
Info "Closed the support-chat window ($count process(es))."
exit 0
