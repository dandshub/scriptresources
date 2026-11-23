package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type Detection struct {
	Type   string `json:"type"` // app | pkg | script
	Value  string `json:"value"`
	Script string `json:"script"`
}

type Installer struct {
	Type              string `json:"type"` // pkg | dmg | zip | script
	URL               string `json:"url"`
	SHA256            string `json:"sha256"`
	InstallScript     string `json:"install_script"`
	UninstallScript   string `json:"uninstall_script"`
	PostInstallScript string `json:"post_install_script"`
}

// Detected describes what is on the machine. Version may be "" for an
// installed item whose version could not be read.
type Detected struct {
	Installed bool
	Version   string
}

func (d Detected) String() string {
	switch {
	case !d.Installed:
		return "not installed"
	case d.Version == "":
		return "installed (unknown version)"
	default:
		return d.Version
	}
}

// appPath normalises an app detection value to an absolute bundle path.
func appPath(v string) string {
	v = strings.TrimSpace(v)
	if !strings.HasSuffix(v, ".app") {
		v += ".app"
	}
	if !filepath.IsAbs(v) {
		v = filepath.Join("/Applications", v)
	}
	return v
}

func detect(d Detection, env map[string]string, alog *ActionLog) (Detected, error) {
	switch d.Type {
	case "app":
		p := appPath(d.Value)
		if st, err := os.Stat(p); err != nil || !st.IsDir() {
			return Detected{}, nil
		}
		v, _ := appVersion(p)
		return Detected{Installed: true, Version: v}, nil
	case "pkg":
		out := cmdOut("/usr/sbin/pkgutil", "--pkg-info", d.Value)
		if out == "" {
			return Detected{}, nil
		}
		for _, line := range strings.Split(out, "\n") {
			if strings.HasPrefix(line, "version:") {
				return Detected{Installed: true, Version: strings.TrimSpace(strings.TrimPrefix(line, "version:"))}, nil
			}
		}
		return Detected{Installed: true}, nil
	case "script":
		// Contract: exit 0 and print the version (last non-empty stdout line)
		// when installed; exit non-zero or print nothing when not installed.
		res, err := RunScript(d.Script, env, 5*time.Minute, alog)
		if err != nil {
			return Detected{}, fmt.Errorf("detection script: %w", err)
		}
		if res.ExitCode != 0 {
			return Detected{}, nil
		}
		lines := strings.Split(strings.TrimSpace(res.Stdout), "\n")
		v := strings.TrimSpace(lines[len(lines)-1])
		if v == "" {
			return Detected{}, nil
		}
		return Detected{Installed: true, Version: v}, nil
	}
	return Detected{}, fmt.Errorf("unknown detection type %q", d.Type)
}

// satisfied reports whether the detected state meets the desired state.
func satisfied(desired, target string, d Detected) bool {
	switch desired {
	case "uninstalled":
		return !d.Installed
	case "installed":
		return d.Installed
	case "latest":
		return d.Installed && (target == "" || d.Version == "" || compareVersions(d.Version, target) >= 0)
	case "version":
		return d.Installed && (d.Version == "" || compareVersions(d.Version, target) == 0)
	}
	return false
}

var installerExt = map[string]string{"pkg": ".pkg", "dmg": ".dmg", "zip": ".zip"}

func installSoftware(det Detection, inst Installer, env map[string]string, alog *ActionLog) error {
	cache := filepath.Join(baseDir(), "cache")
	if err := os.MkdirAll(cache, 0o700); err != nil {
		return err
	}
	work, err := os.MkdirTemp(cache, "install-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(work)

	file := ""
	if inst.URL != "" {
		alog.Printf("Downloading %s", inst.URL)
		file, err = Download(inst.URL, inst.SHA256, work, installerExt[inst.Type], alog.Printf)
		if err != nil {
			return err
		}
	} else if inst.Type != "script" {
		return errors.New("no download URL configured for this software")
	}
	env["MS_INSTALLER_PATH"] = file
	env["MS_DOWNLOAD_DIR"] = work

	switch inst.Type {
	case "script":
		if err := runChecked(inst.InstallScript, env, alog, "install script"); err != nil {
			return err
		}
	case "pkg":
		if err := run(alog, "", "/usr/sbin/installer", "-pkg", file, "-target", "/"); err != nil {
			return err
		}
	case "dmg":
		if err := installFromDMG(file, work, det, alog); err != nil {
			return err
		}
	case "zip":
		dest := filepath.Join(work, "unzipped")
		if err := run(alog, "", "/usr/bin/ditto", "-x", "-k", file, dest); err != nil {
			return err
		}
		if err := installFromDir(dest, det, alog); err != nil {
			return err
		}
	default:
		return fmt.Errorf("unknown installer type %q", inst.Type)
	}
	if strings.TrimSpace(inst.PostInstallScript) != "" {
		alog.Printf("Running post-install script")
		if err := runChecked(inst.PostInstallScript, env, alog, "post-install script"); err != nil {
			return err
		}
	}
	return nil
}

func runChecked(script string, env map[string]string, alog *ActionLog, what string) error {
	res, err := RunScript(script, env, 0, alog)
	if err != nil {
		return fmt.Errorf("%s: %w", what, err)
	}
	if res.ExitCode != 0 {
		return fmt.Errorf("%s exited with code %d", what, res.ExitCode)
	}
	return nil
}

func installFromDMG(file, work string, det Detection, alog *ActionLog) error {
	mnt := filepath.Join(work, "mnt")
	if err := os.MkdirAll(mnt, 0o700); err != nil {
		return err
	}
	// "Y" answers the licence agreement some DMGs present.
	if err := run(alog, "Y\n", "/usr/bin/hdiutil", "attach", file, "-nobrowse", "-readonly",
		"-noautoopen", "-noverify", "-mountpoint", mnt); err != nil {
		return err
	}
	defer run(alog, "", "/usr/bin/hdiutil", "detach", mnt, "-force") //nolint:errcheck
	return installFromDir(mnt, det, alog)
}

// installFromDir installs the right thing from an unpacked DMG/ZIP: the app
// named by the detection rule if present, else a package, else the only app.
func installFromDir(dir string, det Detection, alog *ActionLog) error {
	var apps, pkgs []string
	for _, pattern := range []string{"*", "*/*"} {
		matches, _ := filepath.Glob(filepath.Join(dir, pattern))
		for _, m := range matches {
			if strings.Contains(m, "__MACOSX") {
				continue
			}
			switch strings.ToLower(filepath.Ext(m)) {
			case ".app":
				apps = append(apps, m)
			case ".pkg", ".mpkg":
				pkgs = append(pkgs, m)
			}
		}
	}
	if det.Type == "app" {
		want := filepath.Base(appPath(det.Value))
		for _, a := range apps {
			if filepath.Base(a) == want {
				return copyApp(a, appPath(det.Value), alog)
			}
		}
	}
	if len(pkgs) > 0 {
		return run(alog, "", "/usr/sbin/installer", "-pkg", pkgs[0], "-target", "/")
	}
	if len(apps) == 1 {
		dest := filepath.Join("/Applications", filepath.Base(apps[0]))
		if det.Type == "app" {
			dest = appPath(det.Value)
		}
		return copyApp(apps[0], dest, alog)
	}
	return fmt.Errorf("could not choose what to install: %d app(s), %d package(s) found", len(apps), len(pkgs))
}

func copyApp(src, dest string, alog *ActionLog) error {
	if err := safeAppPath(dest); err != nil {
		return err
	}
	if _, err := os.Stat(dest); err == nil {
		alog.Printf("Removing existing %s", dest)
		if err := os.RemoveAll(dest); err != nil {
			return err
		}
	}
	if err := run(alog, "", "/usr/bin/ditto", src, dest); err != nil {
		return err
	}
	_ = run(alog, "", "/usr/sbin/chown", "-R", "root:wheel", dest)
	_ = run(alog, "", "/usr/bin/xattr", "-dr", "com.apple.quarantine", dest)
	return nil
}

func safeAppPath(p string) error {
	clean := filepath.Clean(p)
	if !filepath.IsAbs(clean) || !strings.HasSuffix(clean, ".app") || strings.Count(clean, "/") < 2 {
		return fmt.Errorf("refusing to modify unsafe app path %q", p)
	}
	return nil
}

func uninstallSoftware(det Detection, inst Installer, env map[string]string, alog *ActionLog) error {
	if strings.TrimSpace(inst.UninstallScript) != "" {
		return runChecked(inst.UninstallScript, env, alog, "uninstall script")
	}
	if det.Type == "app" {
		p := appPath(det.Value)
		if err := safeAppPath(p); err != nil {
			return err
		}
		alog.Printf("Removing %s", p)
		return os.RemoveAll(p)
	}
	return errors.New("no uninstall script configured for this software")
}
