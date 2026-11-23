package main

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"sync"
	"time"
)

// ActionLog collects log lines for one action; they are uploaded with the
// action report and echoed to the agent log.
type ActionLog struct {
	mu    sync.Mutex
	lines []string
}

const maxLogLines = 2000

func (l *ActionLog) Printf(format string, args ...any) {
	line := fmt.Sprintf(format, args...)
	log.Print(line)
	l.mu.Lock()
	defer l.mu.Unlock()
	if len(l.lines) < maxLogLines {
		l.lines = append(l.lines, line)
	} else if len(l.lines) == maxLogLines {
		l.lines = append(l.lines, "... log truncated")
	}
}

func (l *ActionLog) Lines() []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]string(nil), l.lines...)
}

var envName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

// ScriptResult is the outcome of running a script.
type ScriptResult struct {
	Stdout   string
	ExitCode int
}

// RunScript runs script text as root. Scripts starting with "#!" run with
// their own interpreter; anything else runs under /bin/zsh (or /bin/sh where
// zsh is absent). stdout and stderr are streamed into the action log.
func RunScript(script string, env map[string]string, timeout time.Duration, alog *ActionLog) (ScriptResult, error) {
	if strings.TrimSpace(script) == "" {
		return ScriptResult{}, errors.New("empty script")
	}
	if timeout <= 0 {
		timeout = 30 * time.Minute
	}
	f, err := os.CreateTemp("", "macstate-*.sh")
	if err != nil {
		return ScriptResult{}, err
	}
	defer os.Remove(f.Name())
	script = strings.ReplaceAll(script, "\r\n", "\n")
	if _, err := f.WriteString(script); err != nil {
		f.Close()
		return ScriptResult{}, err
	}
	f.Close()
	if err := os.Chmod(f.Name(), 0o700); err != nil {
		return ScriptResult{}, err
	}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	var cmd *exec.Cmd
	if strings.HasPrefix(script, "#!") {
		cmd = exec.CommandContext(ctx, f.Name())
	} else {
		shell := "/bin/zsh"
		if _, err := os.Stat(shell); err != nil {
			shell = "/bin/sh"
		}
		cmd = exec.CommandContext(ctx, shell, f.Name())
	}
	cmd.Env = os.Environ()
	cmd.Env = append(cmd.Env, "MS_CONSOLE_USER="+consoleUser(), "MS_AGENT_VERSION="+Version)
	for k, v := range env {
		if envName.MatchString(k) {
			cmd.Env = append(cmd.Env, k+"="+v)
		} else {
			alog.Printf("skipping invalid parameter name %q", k)
		}
	}
	cmd.Stdin = nil
	cmd.WaitDelay = 10 * time.Second

	var stdout bytes.Buffer
	outPipe, _ := cmd.StdoutPipe()
	errPipe, _ := cmd.StderrPipe()
	if err := cmd.Start(); err != nil {
		return ScriptResult{}, err
	}
	var wg sync.WaitGroup
	stream := func(r io.Reader, prefix string, capture *bytes.Buffer) {
		defer wg.Done()
		sc := bufio.NewScanner(r)
		sc.Buffer(make([]byte, 64*1024), 1<<20)
		for sc.Scan() {
			line := sc.Text()
			if capture != nil {
				capture.WriteString(line + "\n")
			}
			alog.Printf("%s%s", prefix, line)
		}
	}
	wg.Add(2)
	go stream(outPipe, "  | ", &stdout)
	go stream(errPipe, "  ! ", nil)
	wg.Wait()
	err = cmd.Wait()

	res := ScriptResult{Stdout: stdout.String()}
	if ctx.Err() == context.DeadlineExceeded {
		return res, fmt.Errorf("script timed out after %s", timeout)
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		res.ExitCode = ee.ExitCode()
		return res, nil
	}
	return res, err
}

// run executes a system command, logging its output; non-zero exit is an error.
func run(alog *ActionLog, stdin string, name string, args ...string) error {
	alog.Printf("$ %s %s", name, strings.Join(args, " "))
	cmd := exec.Command(name, args...)
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	out, err := cmd.CombinedOutput()
	for _, line := range strings.Split(strings.TrimRight(string(out), "\n"), "\n") {
		if line != "" {
			alog.Printf("  | %s", line)
		}
	}
	if err != nil {
		return fmt.Errorf("%s failed: %w", name, err)
	}
	return nil
}
