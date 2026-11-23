// Command macstate-agent is the MacState endpoint agent for macOS. It runs as
// a root LaunchDaemon, checks in with the MacState server, reports inventory
// and executes maintenance sessions (software install/update/uninstall and
// configuration tasks).
//
// Usage:
//
//	macstate-agent enroll --server URL --key KEY
//	macstate-agent run          # daemon loop (launchd)
//	macstate-agent once         # single check-in, runs a session if queued
//	macstate-agent inventory    # print collected inventory as JSON
//	macstate-agent uninstall    # remove the LaunchDaemon, binary and config
//	macstate-agent version
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// Version is set at build time with -ldflags "-X main.Version=...".
var Version = "dev"

const launchdLabel = "com.macstate.agent"

// baseDir holds config and the download cache. Overridable for testing.
func baseDir() string {
	if d := os.Getenv("MACSTATE_AGENT_HOME"); d != "" {
		return d
	}
	return "/Library/Application Support/MacState"
}

type Config struct {
	Server     string `json:"server"`
	Token      string `json:"token"`
	ComputerID int    `json:"computer_id"`
}

func configPath() string { return filepath.Join(baseDir(), "agent.json") }

func loadConfig() (*Config, error) {
	b, err := os.ReadFile(configPath())
	if err != nil {
		return nil, fmt.Errorf("agent not enrolled (%w); run: macstate-agent enroll --server URL --key KEY", err)
	}
	var c Config
	if err := json.Unmarshal(b, &c); err != nil {
		return nil, fmt.Errorf("bad config %s: %w", configPath(), err)
	}
	if c.Server == "" || c.Token == "" {
		return nil, errors.New("config is missing server or token")
	}
	return &c, nil
}

func saveConfig(c *Config) error {
	if err := os.MkdirAll(baseDir(), 0o755); err != nil {
		return err
	}
	b, _ := json.MarshalIndent(c, "", "  ")
	tmp := configPath() + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, configPath())
}

func main() {
	log.SetFlags(log.LstdFlags)
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: macstate-agent enroll|run|once|inventory|uninstall|version")
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "enroll":
		err = cmdEnroll(os.Args[2:])
	case "run":
		err = cmdRun(false)
	case "once":
		err = cmdRun(true)
	case "inventory":
		b, _ := json.MarshalIndent(map[string]any{"facts": collectFacts(), "inventory": collectInventory()}, "", "  ")
		fmt.Println(string(b))
	case "uninstall":
		err = cmdUninstall()
	case "version", "--version", "-v":
		fmt.Println(Version)
	default:
		err = fmt.Errorf("unknown command %q", os.Args[1])
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func cmdEnroll(args []string) error {
	fs := flag.NewFlagSet("enroll", flag.ExitOnError)
	server := fs.String("server", "", "MacState server URL")
	key := fs.String("key", "", "tenant enrollment key")
	_ = fs.Parse(args)
	if *server == "" || *key == "" {
		return errors.New("--server and --key are required")
	}
	c := &Client{Server: strings.TrimRight(*server, "/")}
	req := struct {
		Facts
		EnrollmentKey string `json:"enrollment_key"`
	}{collectFacts(), *key}
	var resp struct {
		ComputerID int    `json:"computer_id"`
		Token      string `json:"token"`
		Tenant     string `json:"tenant"`
	}
	if err := c.Post("/api/agent/enroll", req, &resp); err != nil {
		return fmt.Errorf("enroll failed: %w", err)
	}
	if err := saveConfig(&Config{Server: c.Server, Token: resp.Token, ComputerID: resp.ComputerID}); err != nil {
		return err
	}
	fmt.Printf("Enrolled as computer %d in tenant %q\n", resp.ComputerID, resp.Tenant)
	return nil
}

func cmdRun(once bool) error {
	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	a := &Agent{client: &Client{Server: cfg.Server, Token: cfg.Token}, sendInventory: true}
	log.Printf("macstate-agent %s starting, server %s", Version, cfg.Server)
	for {
		wait, err := a.Cycle(!once)
		if err != nil {
			log.Printf("check-in failed: %v", err)
			if once {
				return err
			}
			wait = 60 * time.Second
		}
		if once {
			return nil
		}
		time.Sleep(wait)
	}
}

func cmdUninstall() error {
	plist := "/Library/LaunchDaemons/" + launchdLabel + ".plist"
	_ = os.Remove(plist)
	_ = os.RemoveAll(baseDir())
	fmt.Println("MacState agent removed.")
	// Unloading kills this process when invoked by launchd, so do it last.
	_ = exec.Command("launchctl", "bootout", "system/"+launchdLabel).Run()
	return nil
}
