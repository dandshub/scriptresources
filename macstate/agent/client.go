package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"
)

type Client struct {
	Server string
	Token  string
}

type HTTPError struct {
	Status int
	Body   string
}

func (e *HTTPError) Error() string { return fmt.Sprintf("HTTP %d: %s", e.Status, e.Body) }

var apiHTTP = &http.Client{Timeout: 60 * time.Second}

// Post sends in as JSON and decodes the JSON response into out (if non-nil).
func (c *Client) Post(p string, in, out any) error {
	body, err := json.Marshal(in)
	if err != nil {
		return err
	}
	req, err := http.NewRequest("POST", c.Server+p, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "macstate-agent/"+Version)
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	resp, err := apiHTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if resp.StatusCode >= 300 {
		return &HTTPError{Status: resp.StatusCode, Body: strings.TrimSpace(string(data))}
	}
	if out != nil {
		return json.Unmarshal(data, out)
	}
	return nil
}

var downloadHTTP = &http.Client{Timeout: 2 * time.Hour}

// Download fetches rawURL into dir, verifying sha256 when given. Returns the
// file path. The file name comes from Content-Disposition, the final URL, or
// falls back to "download" + defaultExt.
func Download(rawURL, sha, dir, defaultExt string, logf func(string, ...any)) (string, error) {
	req, err := http.NewRequest("GET", rawURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh) macstate-agent/"+Version)
	resp, err := downloadHTTP.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("download %s: HTTP %d", rawURL, resp.StatusCode)
	}
	name := ""
	if _, params, err := mime.ParseMediaType(resp.Header.Get("Content-Disposition")); err == nil {
		name = filepath.Base(params["filename"])
	}
	if name == "" || name == "." || name == "/" {
		if u, err := url.Parse(resp.Request.URL.String()); err == nil {
			name = path.Base(u.Path)
		}
	}
	if name == "" || name == "." || name == "/" {
		name = "download"
	}
	if defaultExt != "" && !strings.EqualFold(filepath.Ext(name), defaultExt) {
		name += defaultExt
	}
	dest := filepath.Join(dir, name)
	f, err := os.Create(dest)
	if err != nil {
		return "", err
	}
	h := sha256.New()
	n, err := io.Copy(io.MultiWriter(f, h), resp.Body)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return "", err
	}
	got := hex.EncodeToString(h.Sum(nil))
	logf("Downloaded %s (%d bytes, sha256 %s)", name, n, got)
	if sha != "" && !strings.EqualFold(sha, got) {
		return "", fmt.Errorf("sha256 mismatch: expected %s, got %s", sha, got)
	}
	return dest, nil
}
