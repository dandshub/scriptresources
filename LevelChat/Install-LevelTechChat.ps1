#############################################################################
#  ____  ____    ____            _                       _   _       _      #
# |  _ \/ ___|  | __ ) _   _ ___(_)_ __   ___  ___ ___  | | | |_   _| |__   #
# | | | \___ \  |  _ \| | | / __| | '_ \ / _ \/ __/ __| | |_| | | | | '_ \  #
# | |_| |___) | | |_) | |_| \__ \ | | | |  __/\__ \__ \ |  _  | |_| | |_) | #
# |____/|____/  |____/ \__,_|___/_|_| |_|\___||___/___/ |_| |_|\__,_|_.__/  #
#                                                                           #
#              LEVEL TECH CHAT  -  3CX Live Chat pop-up                     #
#              Auto-detects a Level.io remote session and opens             #
#              a 3CX Live Chat window on the end-user's desktop.            #
#                                                    Ver 1.0                #
#############################################################################
#
# HOW TO USE
#   1. Fill in the "EDIT ME" section below (at minimum $ChatUrl and, ideally,
#      $RemoteSessionProcesses - see the README for how to find the real name).
#   2. In Level.io, run this whole script on the target device(s) as SYSTEM.
#   3. It installs a per-user watcher that pops the chat window automatically
#      the moment a Level remote-control session is detected.
#
#   Uninstall with Uninstall-LevelTechChat.ps1.
#
#############################################################################


###########
# EDIT ME
###########

# Your company name (used in folder/task naming and window title only).
$CompanyName = "DS Business Hub"

# The 3CX Live Chat URL to open.  Get this from:
#   3CX Admin Console > Live Chat > (your source) > Share > "Copy Link"
# It looks something like:
#   https://yourcompany.3cx.eu/callus/#/<source-id>
# You can test it first by pasting it into a browser.
$ChatUrl = "https://YOURCOMPANY.3cx.eu/callus/#/PASTE-YOUR-SOURCE-ID"

# Process name(s) that only exist WHILE a Level remote-control session is live.
# IMPORTANT: verify these on a real machine. During an active Level remote
# session, open Task Manager > Details, and note the new Level helper .exe
# that appears (and disappears when the session ends). Put its name(s) here.
# Do NOT list the always-on Level agent (e.g. "level"/"level-agent") or the
# chat will open constantly.  If you are unsure, leave this and rely on the
# manual trigger described in the README - it always works.
$RemoteSessionProcesses = @(
    "level-remote-control",
    "level_remote_control",
    "LevelRemoteControl"
)

# Close the chat window automatically when the remote session ends?
$CloseOnDisconnect = $true

# How often (seconds) to check for a remote session.
$PollSeconds = 3

# Chat window size (pixels).
$WindowWidth  = 400
$WindowHeight = 620

# Write a debug log to the install folder?  1 = yes, 0 = no.
$DebugMode = 1


##############################
# DO NOT EDIT PAST THIS POINT
##############################

$ErrorActionPreference = "Stop"

function Get-TimeStamp { return "[{0:yyyy-MM-dd HH:mm:ss}]" -f (Get-Date) }
function Info($m)  { Write-Host "$(Get-TimeStamp) $m" }
function Fail($m)  { Write-Host "$(Get-TimeStamp) [ERROR] $m"; exit 1 }

# ---- Sanity checks -------------------------------------------------------
if ($ChatUrl -match "PASTE-YOUR-SOURCE-ID" -or [string]::IsNullOrWhiteSpace($ChatUrl)) {
    Fail "Set `$ChatUrl to your real 3CX Live Chat share link before deploying."
}

$InstallDir = Join-Path $env:ProgramData "DSBusinessHub\LevelTechChat"
$TaskName   = "DSBusinessHub-LevelTechChat"

Info "Installing $CompanyName Level Tech Chat to $InstallDir"
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

# ---- Write Config.json ---------------------------------------------------
$config = [ordered]@{
    CompanyName             = $CompanyName
    ChatUrl                 = $ChatUrl
    RemoteSessionProcesses  = @($RemoteSessionProcesses)
    CloseOnDisconnect       = [bool]$CloseOnDisconnect
    PollSeconds             = [int]$PollSeconds
    WindowWidth             = [int]$WindowWidth
    WindowHeight            = [int]$WindowHeight
    DebugMode               = [bool]$DebugMode
}
$configPath = Join-Path $InstallDir "Config.json"
$config | ConvertTo-Json | Set-Content -Path $configPath -Encoding UTF8
Info "Wrote config -> $configPath"

# ---- Write Watcher.ps1 ---------------------------------------------------
# NOTE: single-quoted here-string => written verbatim, no expansion here.
$watcher = @'
# LevelTechChat Watcher - runs in the interactive user session.
# Reads Config.json next to it and opens/closes the 3CX chat window.
$ErrorActionPreference = "SilentlyContinue"

$Base       = Split-Path -Parent $MyInvocation.MyCommand.Path
$Cfg        = Get-Content (Join-Path $Base "Config.json") -Raw | ConvertFrom-Json
$LogPath    = Join-Path $Base "watcher.log"
$TriggerFile= Join-Path $Base "open.trigger"
$EdgeDataDir= Join-Path $env:LOCALAPPDATA "DSBH-LevelTechChat-Edge"

function Log($m) {
    if ($Cfg.DebugMode) {
        try {
            if ((Test-Path $LogPath) -and ((Get-Item $LogPath).Length -gt 512KB)) {
                Clear-Content $LogPath
            }
            Add-Content -Path $LogPath -Value ("[{0:yyyy-MM-dd HH:mm:ss}] {1}" -f (Get-Date), $m)
        } catch {}
    }
}

# ---- Single instance guard ----
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, "Global\DSBH-LevelTechChat-Watcher", [ref]$createdNew)
if (-not $createdNew) { Log "Another watcher instance is already running. Exiting."; return }

Log "Watcher started for user $env:USERNAME (session)."

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

function Get-WindowPosition($w, $h) {
    try {
        Add-Type -AssemblyName System.Windows.Forms -EA Stop
        $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
        $x  = [Math]::Max(0, $wa.Right  - $w - 24)
        $y  = [Math]::Max(0, $wa.Bottom - $h - 24)
        return "$x,$y"
    } catch { return "80,80" }
}

function Open-Chat {
    Log "Opening chat window -> $($Cfg.ChatUrl)"
    $w   = [int]$Cfg.WindowWidth
    $h   = [int]$Cfg.WindowHeight
    $pos = Get-WindowPosition $w $h
    $edge = Get-EdgePath
    if ($edge) {
        $argList = @(
            "--app=$($Cfg.ChatUrl)",
            "--window-size=$w,$h",
            "--window-position=$pos",
            "--user-data-dir=$EdgeDataDir",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,FirstRunExperience"
        )
        Start-Process -FilePath $edge -ArgumentList $argList
    } else {
        Log "Edge not found; falling back to default browser."
        Start-Process $Cfg.ChatUrl
    }
}

function Close-Chat {
    Log "Closing chat window(s)."
    try {
        Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -EA SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine.Contains($EdgeDataDir) } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
    } catch {}
}

function Test-RemoteActive {
    foreach ($n in $Cfg.RemoteSessionProcesses) {
        if ([string]::IsNullOrWhiteSpace($n)) { continue }
        $nm = $n -replace '\.exe$', ''
        if (Get-Process -Name $nm -EA SilentlyContinue) { return $true }
    }
    if (Test-Path $TriggerFile) { return $true }
    return $false
}

$shown = $false
$poll  = [int]$Cfg.PollSeconds
if ($poll -lt 1) { $poll = 3 }

while ($true) {
    try {
        $active = Test-RemoteActive
        if ($active -and -not $shown) {
            Open-Chat
            $shown = $true
        }
        elseif (-not $active -and $shown) {
            if ($Cfg.CloseOnDisconnect) { Close-Chat }
            $shown = $false
        }
    } catch {
        Log "Loop error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $poll
}
'@
$watcherPath = Join-Path $InstallDir "Watcher.ps1"
Set-Content -Path $watcherPath -Value $watcher -Encoding UTF8
Info "Wrote watcher -> $watcherPath"

# ---- Write run-hidden.vbs (launches the watcher with no console flash) ---
$vbs = @"
' Launches the LevelTechChat watcher hidden (no console window).
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$watcherPath""", 0, False
"@
$vbsPath = Join-Path $InstallDir "run-hidden.vbs"
Set-Content -Path $vbsPath -Value $vbs -Encoding ASCII
Info "Wrote launcher -> $vbsPath"

# ---- Register the per-user scheduled task --------------------------------
Info "Registering scheduled task '$TaskName'..."
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -EA SilentlyContinue

$action    = New-ScheduledTaskAction -Execute "wscript.exe" -Argument ('"{0}"' -f $vbsPath)
$trigger   = New-ScheduledTaskTrigger -AtLogOn
# Run in the context of whichever user is interactively logged on.
$principal = New-ScheduledTaskPrincipal -GroupId "Users" -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
                -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description "$CompanyName - opens 3CX Live Chat when a Level remote session is detected." | Out-Null
Info "Scheduled task registered."

# ---- Kick it off now for any currently logged-on user --------------------
try {
    Start-ScheduledTask -TaskName $TaskName -EA SilentlyContinue
    Info "Started watcher for the current session (if a user is logged on)."
} catch {
    Info "Could not start immediately; it will start at next logon."
}

Info "DONE. Level Tech Chat is installed."
Info "Test it: create the file '$InstallDir\open.trigger' (e.g. New-Item), the chat should appear within $PollSeconds s; delete it to close."
exit 0
