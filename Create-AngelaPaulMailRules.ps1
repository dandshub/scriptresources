#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!#
# Office 365 / Exchange Online - Mail Flow (Transport) Rules
# Purpose:
#   1. BCC every email SENT BY Angela to Paul, and tag the subject with "Paul's side"
#      so Paul can identify Angela's outgoing side of a conversation.
#   2. BCC every email SENT TO Angela (incoming) to Paul, so Paul sees the other side too.
#
# These are org-level mail flow rules created in Exchange Online. They require an
# account with the "Transport Rules" / Exchange Administrator role.
#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!#


###########
# EDIT ME
###########

# Angela's real mailbox address. The task used "angela.xxx@..." as a placeholder -
# replace it below with her actual primary SMTP address before running.
$AngelaAddress = "angela.xxx@adbusinessrecovery.co.uk"

# Paul's mailbox that receives the copies.
$PaulAddress = "paul.walker@adbusinessrecovery.co.uk"

# Text prepended to the subject of Angela's OUTGOING mail so Paul can identify it.
$SubjectTag = "Paul's side"

# Set to $true to also tag INCOMING mail (mail sent to Angela). Left off by default
# because the request only asked to tag Angela's outgoing side.
$TagIncoming = $false
$IncomingSubjectTag = "To Angela"

# Names of the rules as they will appear in the Exchange admin center.
$OutgoingRuleName = "BCC Paul - Angela outgoing"
$IncomingRuleName = "BCC Paul - Angela incoming"

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

# Make sure the ExchangeOnlineManagement module is present, install for the
# current user if it is missing.
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

# Connect to Exchange Online using modern authentication (a browser / device
# prompt will appear for the admin account).
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

# Create (or update if it already exists) a transport rule.
function Set-Rule ($name, $params) {
    $existing = Get-TransportRule -Identity $name -ErrorAction SilentlyContinue
    if ($existing) {
        Debug-Print "Rule '$name' already exists - updating it."
        Set-TransportRule -Identity $name @params -ErrorAction Stop
    }
    else {
        Debug-Print "Creating rule '$name'."
        New-TransportRule -Name $name @params -ErrorAction Stop
    }
    Write-Host "$(Get-TimeStamp) Rule '$name' is in place."
}

function runMe {
    Debug-Print "Starting..."

    if ($AngelaAddress -like "*angela.xxx@*") {
        throw "Please edit `$AngelaAddress at the top of the script - it is still the placeholder."
    }

    Ensure-EXOModule
    Connect-EXO

    # Rule 1: Angela's OUTGOING mail -> BCC Paul, prepend the subject tag.
    $outgoingParams = @{
        From           = $AngelaAddress
        BlindCopyTo    = $PaulAddress
        PrependSubject = "$SubjectTag " # trailing space keeps it readable
        Comments       = "Auto-created: copies Angela's sent mail to Paul and tags the subject."
        Enabled        = $true
    }
    Set-Rule $OutgoingRuleName $outgoingParams

    # Rule 2: INCOMING mail addressed to Angela -> BCC Paul.
    $incomingParams = @{
        SentTo      = $AngelaAddress
        BlindCopyTo = $PaulAddress
        Comments    = "Auto-created: copies mail sent to Angela over to Paul."
        Enabled     = $true
    }
    if ($TagIncoming) {
        $incomingParams["PrependSubject"] = "$IncomingSubjectTag "
    }
    Set-Rule $IncomingRuleName $incomingParams

    Write-Host "$(Get-TimeStamp) All rules processed successfully."
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
    # Always tidy up the session.
    if (Get-ConnectionInformation -ErrorAction SilentlyContinue) {
        Disconnect-ExchangeOnline -Confirm:$false | Out-Null
        Debug-Print "Disconnected from Exchange Online."
    }
}
