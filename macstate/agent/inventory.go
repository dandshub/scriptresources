package main

import (
	"encoding/json"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"syscall"
	"time"
)

// Facts are sent on every check-in.
type Facts struct {
	Hostname     string `json:"hostname"`
	Serial       string `json:"serial"`
	Model        string `json:"model"`
	OSVersion    string `json:"os_version"`
	Arch         string `json:"arch"`
	AgentVersion string `json:"agent_version"`
	ConsoleUser  string `json:"console_user"`
	IPAddress    string `json:"ip_address"`
}

type App struct {
	Name     string `json:"name"`
	Version  string `json:"version"`
	BundleID string `json:"bundle_id"`
	Path     string `json:"path"`
}

func cmdOut(name string, args ...string) string {
	out, err := exec.Command(name, args...).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func serialNumber() string {
	out := cmdOut("/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice")
	for _, line := range strings.Split(out, "\n") {
		if strings.Contains(line, "IOPlatformSerialNumber") {
			parts := strings.Split(line, "\"")
			if len(parts) >= 4 {
				return parts[3]
			}
		}
	}
	return ""
}

func consoleUser() string {
	u := cmdOut("/usr/bin/stat", "-f%Su", "/dev/console")
	if u == "root" || u == "loginwindow" {
		return ""
	}
	return u
}

func primaryIP() string {
	conn, err := net.Dial("udp", "192.0.2.1:9") // no packets are sent for UDP dial
	if err != nil {
		return ""
	}
	defer conn.Close()
	return conn.LocalAddr().(*net.UDPAddr).IP.String()
}

func collectFacts() Facts {
	name := cmdOut("/usr/sbin/scutil", "--get", "ComputerName")
	if name == "" {
		name, _ = os.Hostname()
	}
	osv := cmdOut("/usr/bin/sw_vers", "-productVersion")
	if osv != "" {
		osv = "macOS " + osv
	} else {
		osv = runtime.GOOS
	}
	return Facts{
		Hostname:     name,
		Serial:       serialNumber(),
		Model:        cmdOut("/usr/sbin/sysctl", "-n", "hw.model"),
		OSVersion:    osv,
		Arch:         runtime.GOARCH,
		AgentVersion: Version,
		ConsoleUser:  consoleUser(),
		IPAddress:    primaryIP(),
	}
}

// readPlist returns the keys of a plist (XML or binary) as a map.
func readPlist(path string) map[string]any {
	out, err := exec.Command("/usr/bin/plutil", "-convert", "json", "-o", "-", path).Output()
	m := map[string]any{}
	if err == nil && json.Unmarshal(out, &m) == nil {
		return m
	}
	// JSON conversion fails on plists containing <data>/<date>; read keys singly.
	for _, k := range []string{"CFBundleShortVersionString", "CFBundleVersion", "CFBundleIdentifier", "CFBundleName"} {
		if v := cmdOut("/usr/bin/plutil", "-extract", k, "raw", "-o", "-", path); v != "" {
			m[k] = v
		}
	}
	return m
}

func plistString(m map[string]any, key string) string {
	if s, ok := m[key].(string); ok {
		return strings.TrimSpace(s)
	}
	return ""
}

// appVersion returns the bundle version of an .app, "" if unknown.
func appVersion(app string) (version, bundleID string) {
	m := readPlist(filepath.Join(app, "Contents", "Info.plist"))
	v := plistString(m, "CFBundleShortVersionString")
	if v == "" {
		v = plistString(m, "CFBundleVersion")
	}
	return v, plistString(m, "CFBundleIdentifier")
}

func listApps() []App {
	var apps []App
	seen := map[string]bool{}
	for _, dir := range []string{"/Applications", "/Applications/Utilities"} {
		entries, _ := filepath.Glob(filepath.Join(dir, "*.app"))
		for _, p := range entries {
			if seen[p] {
				continue
			}
			seen[p] = true
			v, id := appVersion(p)
			apps = append(apps, App{Name: strings.TrimSuffix(filepath.Base(p), ".app"), Version: v, BundleID: id, Path: p})
		}
	}
	sort.Slice(apps, func(i, j int) bool { return strings.ToLower(apps[i].Name) < strings.ToLower(apps[j].Name) })
	return apps
}

func collectInventory() map[string]any {
	inv := map[string]any{
		"collected_at": time.Now().UTC().Format(time.RFC3339),
		"os_build":     cmdOut("/usr/bin/sw_vers", "-buildVersion"),
		"cpu":          cmdOut("/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"),
		"memory_bytes": cmdOut("/usr/sbin/sysctl", "-n", "hw.memsize"),
		"filevault":    cmdOut("/usr/bin/fdesetup", "status"),
		"gatekeeper":   cmdOut("/usr/sbin/spctl", "--status"),
		"sip":          cmdOut("/usr/bin/csrutil", "status"),
		"boot_time":    cmdOut("/usr/sbin/sysctl", "-n", "kern.boottime"),
		"apps":         listApps(),
	}
	var st syscall.Statfs_t
	if syscall.Statfs("/", &st) == nil {
		inv["disk_free_bytes"] = uint64(st.Bavail) * uint64(st.Bsize)
		inv["disk_total_bytes"] = uint64(st.Blocks) * uint64(st.Bsize)
	}
	return inv
}
