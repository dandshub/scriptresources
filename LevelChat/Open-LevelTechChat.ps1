#############################################################################
#  DS Business Hub - Level Tech Chat  :  OPEN CHAT (on-demand)              #
#                                                                           #
#  The SIMPLE option. Run this from Level.io as an on-demand script the     #
#  moment you connect to a machine. It pops your 3CX Live Chat window on    #
#  the end-user's desktop - no background service, no install, nothing      #
#  left running.                                                            #
#                                                                           #
#  Why the scheduled-task dance below?  Level runs scripts as SYSTEM, and   #
#  SYSTEM cannot draw a window on the user's desktop (session-0 isolation). #
#  So we briefly borrow the logged-on user's session to launch the window,  #
#  then clean up.                                                           #
#                                                    Ver 1.0                #
#############################################################################


###########
# EDIT ME
###########

# Your 3CX Live Chat share link.
#   3CX Admin Console > Live Chat > (your source) > Share > "Copy Link"
$ChatUrl = "https://YOURCOMPANY.3cx.eu/callus/#/PASTE-YOUR-SOURCE-ID"

# Chat window size (pixels).
$WindowWidth  = 400
$WindowHeight = 620


##############################
# DO NOT EDIT PAST THIS POINT
##############################

$ErrorActionPreference = "Stop"
function Get-TimeStamp { return "[{0:yyyy-MM-dd HH:mm:ss}]" -f (Get-Date) }
function Info($m) { Write-Host "$(Get-TimeStamp) $m" }
function Fail($m) { Write-Host "$(Get-TimeStamp) [ERROR] $m"; exit 1 }

if ($ChatUrl -match "PASTE-YOUR-SOURCE-ID" -or [string]::IsNullOrWhiteSpace($ChatUrl)) {
    Fail "Set `$ChatUrl to your real 3CX Live Chat share link first."
}

# ---- Find the logged-on interactive user (owner of explorer.exe) ---------
$explorer = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -EA SilentlyContinue)
if ($explorer.Count -eq 0) { Fail "No user is logged on interactively - nothing to show a window to." }
$owner = Invoke-CimMethod -InputObject $explorer[0] -MethodName GetOwner
$targetUser = "$($owner.Domain)\$($owner.User)"
Info "Logged-on user: $targetUser"

# ---- Resolve Edge (used in borderless 'app' mode) ------------------------
function Get-EdgePath {
    $paths = @(
        (Join-Path $env:ProgramFiles       "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe")
    )
    foreach ($p in $paths) { if ($p -and (Test-Path $p)) { return $p } }
    $ap = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe" -EA SilentlyContinue).'(default)'
    if ($ap -and (Test-Path $ap)) { return $ap }
    return $null
}

$edge = Get-EdgePath
if ($edge) {
    $exec = $edge
    $args = "--app=$ChatUrl --window-size=$WindowWidth,$WindowHeight --no-first-run --no-default-browser-check"
} else {
    # Fallback: open in the user's default browser.
    Info "Edge not found; using the default browser."
    $exec = "$env:SystemRoot\System32\cmd.exe"
    $args = "/c start `"`" `"$ChatUrl`""
}

# ---- Launch it inside the user's session via a throwaway task ------------
$TaskName = "DSBusinessHub-OpenChat-$([Guid]::NewGuid().ToString('N').Substring(0,8))"
try {
    $action    = New-ScheduledTaskAction -Execute $exec -Argument $args
    $principal = New-ScheduledTaskPrincipal -UserId $targetUser -LogonType Interactive -RunLevel Limited
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    Register-ScheduledTask -TaskName $TaskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Info "Chat window launched on $targetUser's desktop."
    Start-Sleep -Seconds 3
}
catch {
    Fail "Failed to launch chat: $($_.Exception.Message)"
}
finally {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -EA SilentlyContinue
}

exit 0
