package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

type Agent struct {
	client        *Client
	sendInventory bool
}

type Action struct {
	ID   int    `json:"id"`
	Type string `json:"type"` // software | task | script
	Name string `json:"name"`
	// software
	Desired       string    `json:"desired"`
	TargetVersion string    `json:"target_version"`
	Detection     Detection `json:"detection"`
	Installer     Installer `json:"installer"`
	// task
	TestScript string            `json:"test_script"`
	SetScript  string            `json:"set_script"`
	Parameters map[string]string `json:"parameters"`
	// script
	Script  string `json:"script"`
	Timeout int    `json:"timeout"`
}

type Session struct {
	ID      int      `json:"id"`
	Mode    string   `json:"mode"` // apply | detect
	Kind    string   `json:"kind"`
	Actions []Action `json:"actions"`
}

type CheckinResponse struct {
	PollSeconds   int      `json:"poll_seconds"`
	Session       *Session `json:"session"`
	WantInventory bool     `json:"want_inventory"`
	Agent         *struct {
		Version string            `json:"version"`
		SHA256  map[string]string `json:"sha256"`
	} `json:"agent"`
}

type Report struct {
	Status         string   `json:"status"`
	DetectedBefore string   `json:"detected_before"`
	DetectedAfter  string   `json:"detected_after"`
	Message        string   `json:"message"`
	Log            []string `json:"log"`
}

var errCancelled = errors.New("session cancelled on server")

// Cycle performs one check-in and runs any session handed back. It returns
// how long to wait before the next check-in.
func (a *Agent) Cycle(allowUpdate bool) (time.Duration, error) {
	req := struct {
		Facts
		Inventory map[string]any `json:"inventory,omitempty"`
	}{Facts: collectFacts()}
	if a.sendInventory {
		req.Inventory = collectInventory()
	}
	var resp CheckinResponse
	if err := a.client.Post("/api/agent/checkin", req, &resp); err != nil {
		return 0, err
	}
	a.sendInventory = resp.WantInventory
	wait := time.Duration(resp.PollSeconds) * time.Second
	if wait <= 0 {
		wait = 60 * time.Second
	}
	if resp.Session != nil {
		a.runSession(resp.Session)
		a.sendInventory = true // report post-maintenance state promptly
		return 2 * time.Second, nil
	}
	if allowUpdate && resp.Agent != nil {
		if err := a.selfUpdate(resp.Agent.Version, resp.Agent.SHA256[runtime.GOARCH]); err != nil {
			log.Printf("self-update failed: %v", err)
		}
	}
	return wait, nil
}

func (a *Agent) report(actionID int, r Report) error {
	err := a.client.Post(fmt.Sprintf("/api/agent/actions/%d", actionID), r, nil)
	var he *HTTPError
	if errors.As(err, &he) && he.Status == 409 {
		return errCancelled
	}
	return err
}

func (a *Agent) runSession(s *Session) {
	log.Printf("session %d (%s, %s): %d action(s)", s.ID, s.Kind, s.Mode, len(s.Actions))
	for _, act := range s.Actions {
		if err := a.report(act.ID, Report{Status: "running"}); err != nil {
			if errors.Is(err, errCancelled) {
				log.Printf("session %d cancelled", s.ID)
				return
			}
			log.Printf("report failed: %v", err)
		}
		alog := &ActionLog{}
		alog.Printf("== %s (%s, desired: %s)", act.Name, act.Type, act.Desired)
		r := a.execute(s.Mode, act, alog)
		r.Log = alog.Lines()
		err := a.report(act.ID, r)
		for attempt := 1; err != nil && !errors.Is(err, errCancelled) && attempt <= 3; attempt++ {
			time.Sleep(time.Duration(attempt*5) * time.Second)
			err = a.report(act.ID, r)
		}
		if errors.Is(err, errCancelled) {
			log.Printf("session %d cancelled", s.ID)
			return
		}
	}
	if err := a.client.Post(fmt.Sprintf("/api/agent/sessions/%d/finish", s.ID), map[string]string{}, nil); err != nil {
		log.Printf("finish session %d: %v", s.ID, err)
	}
}

func (a *Agent) execute(mode string, act Action, alog *ActionLog) (r Report) {
	defer func() {
		if p := recover(); p != nil {
			r = Report{Status: "failed", Message: fmt.Sprintf("agent panic: %v", p)}
		}
	}()
	switch act.Type {
	case "software":
		return a.doSoftware(mode, act, alog)
	case "task":
		return a.doTask(mode, act, alog)
	case "script":
		timeout := time.Duration(act.Timeout) * time.Second
		res, err := RunScript(act.Script, map[string]string{}, timeout, alog)
		if err != nil {
			return Report{Status: "failed", Message: err.Error()}
		}
		if res.ExitCode != 0 {
			return Report{Status: "failed", Message: fmt.Sprintf("exit code %d", res.ExitCode)}
		}
		return Report{Status: "succeeded", Message: "exit code 0"}
	}
	return Report{Status: "failed", Message: "unknown action type " + act.Type}
}

func (a *Agent) doSoftware(mode string, act Action, alog *ActionLog) Report {
	env := map[string]string{"MS_TARGET_VERSION": act.TargetVersion}
	if act.Detection.Type == "app" {
		env["MS_APP_PATH"] = appPath(act.Detection.Value)
	}
	before, err := detect(act.Detection, env, alog)
	if err != nil {
		return Report{Status: "failed", Message: err.Error()}
	}
	r := Report{DetectedBefore: before.String()}
	env["MS_DETECTED_VERSION"] = before.Version
	alog.Printf("Detected: %s; target: %s %s", before, act.Desired, act.TargetVersion)

	if satisfied(act.Desired, act.TargetVersion, before) {
		r.Status, r.Message = "compliant", "already in desired state"
		return r
	}
	if mode == "detect" {
		r.Status, r.Message = "noncompliant", describeGap(act, before)
		return r
	}

	if act.Desired == "uninstalled" {
		err = uninstallSoftware(act.Detection, act.Installer, env, alog)
	} else {
		if act.Desired == "version" && before.Installed && compareVersions(before.Version, act.TargetVersion) > 0 {
			alog.Printf("Installed version is newer than %s; uninstalling first", act.TargetVersion)
			if err = uninstallSoftware(act.Detection, act.Installer, env, alog); err != nil {
				r.Status, r.Message = "failed", "downgrade uninstall: "+err.Error()
				return r
			}
		}
		err = installSoftware(act.Detection, act.Installer, env, alog)
	}
	if err != nil {
		r.Status, r.Message = "failed", err.Error()
		return r
	}

	after, err := detect(act.Detection, env, alog)
	if err != nil {
		r.Status, r.Message = "failed", "post-change detection: "+err.Error()
		return r
	}
	r.DetectedAfter = after.String()
	alog.Printf("Detected after change: %s", after)
	if !satisfied(act.Desired, act.TargetVersion, after) {
		r.Status, r.Message = "failed", "post-change detection does not match desired state ("+after.String()+")"
		return r
	}
	switch {
	case act.Desired == "uninstalled":
		r.Status = "uninstalled"
	case before.Installed:
		r.Status = "updated"
	default:
		r.Status = "installed"
	}
	r.Message = fmt.Sprintf("%s -> %s", before, after)
	return r
}

func describeGap(act Action, d Detected) string {
	switch act.Desired {
	case "uninstalled":
		return "installed (" + d.String() + "), should be removed"
	case "installed":
		return "not installed"
	default:
		if !d.Installed {
			return "not installed"
		}
		return fmt.Sprintf("version %s, wants %s", d, act.TargetVersion)
	}
}

func (a *Agent) doTask(mode string, act Action, alog *ActionLog) Report {
	env := map[string]string{}
	for k, v := range act.Parameters {
		env[k] = v
	}
	test := func() (bool, error) {
		alog.Printf("Running test script")
		res, err := RunScript(act.TestScript, env, 10*time.Minute, alog)
		if err != nil {
			return false, err
		}
		return res.ExitCode == 0, nil
	}
	ok, err := test()
	if err != nil {
		return Report{Status: "failed", Message: "test: " + err.Error()}
	}
	r := Report{DetectedBefore: map[bool]string{true: "compliant", false: "non-compliant"}[ok]}
	if ok {
		r.Status = "compliant"
		return r
	}
	if mode == "detect" || act.Desired == "monitor" {
		r.Status, r.Message = "noncompliant", "test script failed"
		return r
	}
	if strings.TrimSpace(act.SetScript) == "" {
		r.Status, r.Message = "noncompliant", "no set script to remediate"
		return r
	}
	alog.Printf("Running set script")
	if err := runChecked(act.SetScript, env, alog, "set script"); err != nil {
		r.Status, r.Message = "failed", err.Error()
		return r
	}
	ok, err = test()
	if err != nil {
		r.Status, r.Message = "failed", "re-test: "+err.Error()
		return r
	}
	if !ok {
		r.DetectedAfter = "non-compliant"
		r.Status, r.Message = "failed", "still non-compliant after set script"
		return r
	}
	r.DetectedAfter = "compliant"
	r.Status, r.Message = "remediated", "set script brought the device into compliance"
	return r
}

// selfUpdate replaces this binary with the server's build when the versions
// differ, then exits so launchd (KeepAlive) restarts the new agent.
func (a *Agent) selfUpdate(version, sha string) error {
	if version == "" || version == Version || sha == "" || Version == "dev" {
		return nil
	}
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	if exe, err = filepath.EvalSymlinks(exe); err != nil {
		return err
	}
	log.Printf("updating agent %s -> %s", Version, version)
	resp, err := downloadHTTP.Get(a.client.Server + "/agent/download/" + runtime.GOARCH)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("download: HTTP %d", resp.StatusCode)
	}
	tmp := exe + ".new"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
	if err != nil {
		return err
	}
	h := sha256.New()
	_, err = io.Copy(io.MultiWriter(f, h), resp.Body)
	f.Close()
	if err != nil {
		os.Remove(tmp)
		return err
	}
	if got := hex.EncodeToString(h.Sum(nil)); !strings.EqualFold(got, sha) {
		os.Remove(tmp)
		return fmt.Errorf("sha256 mismatch (got %s)", got)
	}
	if err := os.Rename(tmp, exe); err != nil {
		os.Remove(tmp)
		return err
	}
	log.Printf("agent updated to %s; exiting for launchd restart", version)
	os.Exit(0)
	return nil
}
