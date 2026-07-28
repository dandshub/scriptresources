#############################################################################
#  DS Business Hub - Level Tech Chat  :  OPEN CHAT (tech-initiated)         #
#                                                                           #
#  Run this from Level.io as an on-demand script the moment you connect to  #
#  a machine. It pops your 3CX Live Chat window on the end-user's desktop.  #
#  No install, no background service, nothing left running.                 #
#                                                                           #
#  Why the scheduled-task dance below?  Level runs scripts as SYSTEM, and   #
#  SYSTEM cannot draw a window on the user's desktop (session-0 isolation). #
#  So we briefly borrow the logged-on user's session to launch the window,  #
#  then delete the task. The chat window keeps running.                     #
#                                                    Ver 1.2                #
#############################################################################


###########
# EDIT ME
###########

# --- How the chat page is delivered -------------------------------------
# OPTION 1 (recommended): host chat.html on your website and put its URL here.
#   e.g. "https://dsbusinesshub.co.uk/support-chat.html"
#   Then also set 3CX > Live Chat > "Your website" to that domain.
# OPTION 2 (zero hosting): leave $HostedChatUrl blank. This script will drop a
#   local copy of the chat page on the PC (built from the 3CX values below)
#   and open that. Usually works; 3CX can refuse a file:// page if its domain
#   restriction is strict - if so, switch to OPTION 1.
$HostedChatUrl = ""

# 3CX values (used only for OPTION 2, the local page). From your embed snippet:
$PbxUrl = "https://1303.3cx.cloud"
$Party  = "LiveChat595749"

# Chat window size (pixels).
$WindowWidth  = 400
$WindowHeight = 640


##############################
# DO NOT EDIT PAST THIS POINT
##############################

$ErrorActionPreference = "Stop"
function Get-TimeStamp { return "[{0:yyyy-MM-dd HH:mm:ss}]" -f (Get-Date) }
function Info($m) { Write-Host "$(Get-TimeStamp) $m" }
function Fail($m) { Write-Host "$(Get-TimeStamp) [ERROR] $m"; exit 1 }

# Tag baked into the Edge profile path so Close-LevelTechChat.ps1 can find and
# close exactly this window (and nothing else the user has open).
$ProfileTag = "DSBH-LevelTechChat-Edge"
$InstallDir = Join-Path $env:ProgramData "DSBusinessHub\LevelTechChat"

# ---- Work out what URL to open -------------------------------------------
if (-not [string]::IsNullOrWhiteSpace($HostedChatUrl)) {
    $target = $HostedChatUrl
    Info "Using hosted chat page: $target"
}
else {
    # Build a local chat.html from the 3CX values and open it via file://
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    $htmlPath = Join-Path $InstallDir "chat.html"
    $html = @"
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DS Business Hub - Support Chat</title>
  <style>
    html, body { height:100%; margin:0; background:#f4f6f8; font-family:"Segoe UI",Arial,sans-serif; }
    .intro { padding:18px 16px; text-align:center; color:#2b2f33; }
    .intro h1 { font-size:16px; margin:6px 0; }
    .intro p  { font-size:13px; color:#6a7178; margin:0; }
  </style>
</head>
<body>
  <div class="intro">
    <h1>DS Business Hub &mdash; Support Chat</h1>
    <p>You're connected to our support team. Type below to chat with us.</p>
  </div>
  <call-us
    phonesystem-url="$PbxUrl"
    party="$Party"
    id="wp-live-chat-by-3CX"
    minimized="false"
    animation-style="noanimation"
    allow-call="false"
    allow-video="false"
    enable="true"
    authentication="none"
    operator-name="DS Business Hub Tech"
    show-operator-actual-name="true"
    gdpr-enabled="false"
    message-userinfo-format="both"
    lang="browser"
    greeting-visibility="none"
    button-icon-type="default"
    chat-delay="0"
    style="position:fixed;right:16px;bottom:16px;z-index:99999;font-size:16px;line-height:17px;"
  ></call-us>
  <script defer src="https://downloads-global.3cx.com/downloads/livechatandtalk/v1/callus.js" id="tcx-callus-js" charset="utf-8"></script>
  <script>
    (function () {
      var tries = 0;
      var timer = setInterval(function () {
        tries++;
        var host = document.querySelector('call-us');
        if (host && host.shadowRoot) {
          var nodes = host.shadowRoot.querySelectorAll(
            'button,[role=button],a,[class*=bubble],[class*=launch],[class*=btn],[class*=callus]');
          for (var i = 0; i < nodes.length; i++) {
            var r = nodes[i].getBoundingClientRect();
            if (r.width > 0 && r.height > 0) { nodes[i].click(); clearInterval(timer); return; }
          }
        }
        if (tries > 40) clearInterval(timer);
      }, 400);
    })();
  </script>
</body>
</html>
"@
    Set-Content -Path $htmlPath -Value $html -Encoding UTF8
    $target = "file:///" + ($htmlPath -replace '\\', '/')
    Info "Using local chat page: $htmlPath"
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

# We always launch via cmd's "start" so that:
#   * %LOCALAPPDATA% expands in the USER's context (we run as SYSTEM), and
#   * Edge is detached and survives after this script/task ends.
$cmd = "$env:SystemRoot\System32\cmd.exe"
$edge = Get-EdgePath
if ($edge) {
    $profileDir = "%LOCALAPPDATA%\$ProfileTag"
    $launchArgs = "/c start `"`" `"$edge`" --app=$target --window-size=$WindowWidth,$WindowHeight --user-data-dir=`"$profileDir`" --no-first-run --no-default-browser-check"
} else {
    # Fallback: open in the user's default browser (no isolated profile / no
    # targeted close - user closes it themselves).
    Info "Edge not found; using the default browser."
    $launchArgs = "/c start `"`" `"$target`""
}

# ---- Launch it inside the user's session via a throwaway task ------------
$TaskName = "DSBusinessHub-OpenChat-$([Guid]::NewGuid().ToString('N').Substring(0,8))"
try {
    $action    = New-ScheduledTaskAction -Execute $cmd -Argument $launchArgs
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
