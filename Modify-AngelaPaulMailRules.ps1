#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!#
# Office 365 / Exchange Online - Modify existing Angela -> Paul mail flow rules (OPTION 2)
# Purpose:
#   The original rules tagged Angela's OUTGOING subject with "Paul's side". That subject
#   change is visible to every real recipient (and comes back on their replies), so it is
#   not discreet. This script switches to a hidden approach:
#     * Removes the visible subject prepend from the outgoing rule.
#     * Stamps a hidden header on BOTH copies sent to Paul so he can identify / auto-file them:
#         X-Monitor: Angela-Out   (mail Angela sent)
#         X-Monitor: Angela-In    (mail sent to Angela)
#   The header is invisible to Angela and to her correspondents; only Paul filters on it.
#
# Run with an account that holds the Exchange Administrator / Transport Rules role.
# Assumes the rules already exist (created by Create-AngelaPaulMailRules.ps1).
#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!#


###########
# EDIT ME
###########

# Must match the rule names used when the rules were first created.
$OutgoingRuleName = "BCC Paul - Angela outgoing"
$IncomingRuleName = "BCC Paul - Angela incoming"

# Hidden header Paul filters on. Same header name for both, different value.
$HeaderName        = "X-Monitor"
$OutgoingHeaderVal = "Angela-Out"
$IncomingHeaderVal = "Angela-In"

# Enable extra console logging with 1
$DebugMode = 1


##############################
# DO NOT EDIT PAST THIS POINT
##############################

function Get-TimeStamp {
    return "[{0:MM/dd/yy} {0:HH:mm:ss}]" -f (Get-Date)
}

function Debug-Print ($message) {
    if ($DebugMode -eq 1) {
        Write-Host "$(Get-TimeStamp) [DEBUG] $message"
    }
}

function Ensure-EXOModule {
    Debug-Print "Checking for the ExchangeOnlineManagement module..."
    if (-not (Get-Module -ListAvailable -Name ExchangeOnlineManagement)) {
        Debug-Print "Module not found - installing for current user..."
        try {
            Install-Module -Name ExchangeOnlineManagement -Scope CurrentUser -Force -AllowClobber -ErrorAction Stop
        }
        catch {
            throw "Failed to install ExchangeOnlineManagement: $($_.Exception.Message)"
        }
    }
    Import-Module ExchangeOnlineManagement -ErrorAction Stop
    Debug-Print "ExchangeOnlineManagement module ready."
}

function Connect-EXO {
    Debug-Print "Connecting to Exchange Online..."
    try {
        Connect-ExchangeOnline -ShowBanner:$false -ErrorAction Stop
    }
    catch {
        throw "Failed to connect to Exchange Online: $($_.Exception.Message)"
    }
    Debug-Print "Connected."
}

# Confirm a rule exists before we try to change it.
function Get-RuleOrFail ($name) {
    $rule = Get-TransportRule -Identity $name -ErrorAction SilentlyContinue
    if (-not $rule) {
        throw "Rule '$name' was not found. Run Create-AngelaPaulMailRules.ps1 first, or check the rule name."
    }
    return $rule
}

function runMe {
    Debug-Print "Starting..."

    Ensure-EXOModule
    Connect-EXO

    # --- Outgoing rule: drop the visible subject tag, add the hidden header. ---
    Get-RuleOrFail $OutgoingRuleName | Out-Null
    Debug-Print "Updating '$OutgoingRuleName' - removing subject prepend, adding header."
    Set-TransportRule -Identity $OutgoingRuleName `
        -PrependSubject $null `
        -SetHeaderName $HeaderName `
        -SetHeaderValue $OutgoingHeaderVal `
        -ErrorAction Stop
    Write-Host "$(Get-TimeStamp) '$OutgoingRuleName' updated: subject tag removed, $HeaderName=$OutgoingHeaderVal set."

    # --- Incoming rule: add the hidden header (never had a subject tag). ---
    Get-RuleOrFail $IncomingRuleName | Out-Null
    Debug-Print "Updating '$IncomingRuleName' - adding header."
    Set-TransportRule -Identity $IncomingRuleName `
        -SetHeaderName $HeaderName `
        -SetHeaderValue $IncomingHeaderVal `
        -ErrorAction Stop
    Write-Host "$(Get-TimeStamp) '$IncomingRuleName' updated: $HeaderName=$IncomingHeaderVal set."

    Write-Host ""
    Write-Host "$(Get-TimeStamp) Done. Paul can now auto-file the copies with an Outlook rule:"
    Write-Host "    Condition: 'with specific words in the message header'"
    Write-Host "    Value:     $HeaderName`: $OutgoingHeaderVal   -> Angela's outgoing folder"
    Write-Host "    Value:     $HeaderName`: $IncomingHeaderVal    -> mail sent to Angela folder"
}

try {
    runMe
}
catch {
    $ErrorMsg = $_.Exception.Message
    Write-Host "$(Get-TimeStamp) ERROR: $ErrorMsg"
    exit 1
}
finally {
    if (Get-ConnectionInformation -ErrorAction SilentlyContinue) {
        Disconnect-ExchangeOnline -Confirm:$false | Out-Null
        Debug-Print "Disconnected from Exchange Online."
    }
}
