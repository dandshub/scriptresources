package main

import (
	"regexp"
	"strings"
)

var versionToken = regexp.MustCompile(`\d+|[A-Za-z]+`)

func isDigits(s string) bool {
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return s != ""
}

// compareVersions compares dotted version strings, returning -1, 0 or 1.
// Numeric tokens compare numerically; a trailing alphabetic token marks a
// pre-release, so 1.0b1 < 1.0 < 1.0.1. Mirrors server/engine.py.
func compareVersions(a, b string) int {
	ta := versionToken.FindAllString(a, -1)
	tb := versionToken.FindAllString(b, -1)
	n := len(ta)
	if len(tb) > n {
		n = len(tb)
	}
	for i := 0; i < n; i++ {
		var x, y string
		xOK, yOK := i < len(ta), i < len(tb)
		if xOK {
			x = ta[i]
		}
		if yOK {
			y = tb[i]
		}
		if !xOK {
			if isDigits(y) {
				x = "0"
			} else {
				return 1
			}
		}
		if !yOK {
			if isDigits(x) {
				y = "0"
			} else {
				return -1
			}
		}
		xd, yd := isDigits(x), isDigits(y)
		switch {
		case xd && yd:
			// Compare arbitrarily long integers: strip leading zeros, then
			// a longer number is bigger, else compare lexically.
			xs, ys := strings.TrimLeft(x, "0"), strings.TrimLeft(y, "0")
			if len(xs) != len(ys) {
				if len(xs) < len(ys) {
					return -1
				}
				return 1
			}
			if xs != ys {
				if xs < ys {
					return -1
				}
				return 1
			}
		case xd != yd:
			if xd {
				return 1
			}
			return -1
		default:
			xl, yl := strings.ToLower(x), strings.ToLower(y)
			if xl != yl {
				if xl < yl {
					return -1
				}
				return 1
			}
		}
	}
	return 0
}
