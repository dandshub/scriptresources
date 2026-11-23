"""First-run bootstrap: admin user, default tenant and a starter library."""
from __future__ import annotations

import json
import logging
import secrets

from . import config
from .db import DB, now
from .security import hash_password, new_enrollment_key

log = logging.getLogger("macstate")

SOFTWARE = [
    {
        "name": "Google Chrome", "publisher": "Google",
        "detection_type": "app", "detection_value": "/Applications/Google Chrome.app",
        "installer_type": "dmg",
        "download_url": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
    },
    {
        "name": "Mozilla Firefox", "publisher": "Mozilla",
        "detection_type": "app", "detection_value": "/Applications/Firefox.app",
        "installer_type": "dmg",
        "download_url": "https://download.mozilla.org/?product=firefox-latest-ssl&os=osx&lang=en-GB",
    },
    {
        "name": "Zoom Workplace", "publisher": "Zoom",
        "detection_type": "app", "detection_value": "/Applications/zoom.us.app",
        "installer_type": "pkg",
        "download_url": "https://zoom.us/client/latest/ZoomInstallerIT.pkg",
    },
    {
        "name": "Visual Studio Code", "publisher": "Microsoft",
        "detection_type": "app", "detection_value": "/Applications/Visual Studio Code.app",
        "installer_type": "zip",
        "download_url": "https://update.code.visualstudio.com/latest/darwin-universal/stable",
    },
    {
        "name": "Microsoft Teams", "publisher": "Microsoft",
        "detection_type": "app", "detection_value": "/Applications/Microsoft Teams.app",
        "installer_type": "pkg",
        "download_url": "https://go.microsoft.com/fwlink/?linkid=2249065",
    },
    {
        "name": "Homebrew", "publisher": "Homebrew",
        "description": "Installs Homebrew for the current console user.",
        "detection_type": "script",
        "detection_script": (
            "for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do\n"
            "  [ -x \"$b\" ] && { \"$b\" --version 2>/dev/null | head -1 | awk '{print $2}'; exit 0; }\n"
            "done\nexit 1\n"
        ),
        "installer_type": "script",
        "install_script": (
            "#!/bin/bash\nset -e\n"
            "user=$(stat -f%Su /dev/console)\n"
            "[ \"$user\" = root ] && { echo 'No console user logged in'; exit 1; }\n"
            "xcode-select -p >/dev/null 2>&1 || { echo 'Command Line Tools required'; exit 1; }\n"
            "curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o /tmp/brew-install.sh\n"
            "chmod 755 /tmp/brew-install.sh\n"
            "NONINTERACTIVE=1 sudo -u \"$user\" -H /bin/bash /tmp/brew-install.sh\n"
        ),
    },
]

TASKS = [
    {
        "name": "Firewall enabled",
        "description": "Ensures the macOS application firewall is on.",
        "test_script": "/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate | grep -q enabled\n",
        "set_script": "/usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on\n",
    },
    {
        "name": "Gatekeeper enabled",
        "description": "Ensures Gatekeeper assessments are enabled.",
        "test_script": "spctl --status 2>&1 | grep -q 'assessments enabled'\n",
        "set_script": "spctl --global-enable 2>/dev/null || spctl --master-enable\n",
    },
    {
        "name": "FileVault enabled",
        "description": "Reports FileVault status. Enabling needs a user secure token, so deploy as Monitor.",
        "test_script": "fdesetup status | grep -q 'FileVault is On'\n",
        "set_script": "",
    },
    {
        "name": "Automatic macOS updates",
        "description": "Turns on automatic checking, downloading and installing of macOS and security updates.",
        "test_script": (
            "p=/Library/Preferences/com.apple.SoftwareUpdate\n"
            "for k in AutomaticCheckEnabled AutomaticDownload CriticalUpdateInstall; do\n"
            "  [ \"$(defaults read $p $k 2>/dev/null)\" = 1 ] || exit 1\ndone\n"
        ),
        "set_script": (
            "p=/Library/Preferences/com.apple.SoftwareUpdate\n"
            "for k in AutomaticCheckEnabled AutomaticDownload CriticalUpdateInstall; do\n"
            "  defaults write $p $k -bool true\ndone\n"
        ),
    },
    {
        "name": "Computer name matches pattern",
        "description": "Renames the Mac to PREFIX-SERIAL. Parameter: PREFIX.",
        "test_script": (
            "want=\"${PREFIX}-$(ioreg -rd1 -c IOPlatformExpertDevice | awk -F'\"' '/IOPlatformSerialNumber/{print $4}')\"\n"
            "[ \"$(scutil --get ComputerName)\" = \"$want\" ]\n"
        ),
        "set_script": (
            "want=\"${PREFIX}-$(ioreg -rd1 -c IOPlatformExpertDevice | awk -F'\"' '/IOPlatformSerialNumber/{print $4}')\"\n"
            "for k in ComputerName LocalHostName HostName; do scutil --set $k \"$want\"; done\n"
        ),
        "parameters": {"PREFIX": "MAC"},
    },
]


def bootstrap(db: DB) -> None:
    if not db.scalar("SELECT COUNT(*) FROM users"):
        pw = config.ADMIN_PASSWORD or secrets.token_urlsafe(12)
        db.run("INSERT INTO users(username,pw_hash,role) VALUES(?,?,?)",
               ("admin", hash_password(pw), "admin"))
        if not config.ADMIN_PASSWORD:
            log.warning("Created initial user 'admin' with password: %s", pw)
            print(f"[macstate] Created initial user 'admin' with password: {pw}", flush=True)

    if not db.scalar("SELECT COUNT(*) FROM tenants"):
        db.run("INSERT INTO tenants(name,enrollment_key,created_at) VALUES(?,?,?)",
               ("Default", new_enrollment_key(), now()))

    if config.SEED and not db.get_setting("seeded"):
        for s in SOFTWARE:
            row = {"publisher": "", "description": "", "detection_value": "", "detection_script": "",
                   "download_url": "", "install_script": "", "uninstall_script": "",
                   "post_install_script": "", **s}
            cols = list(row)
            db.run(f"INSERT OR IGNORE INTO software({','.join(cols)},created_at) "
                   f"VALUES({','.join('?' * len(cols))},?)", [row[c] for c in cols] + [now()])
        for t in TASKS:
            db.run("INSERT OR IGNORE INTO tasks(name,description,test_script,set_script,parameters_json,created_at) "
                   "VALUES(?,?,?,?,?,?)",
                   (t["name"], t["description"], t["test_script"], t["set_script"],
                    json.dumps(t.get("parameters", {})), now()))
        db.set_setting("seeded", "1")
