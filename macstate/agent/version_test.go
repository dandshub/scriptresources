package main

import "testing"

func TestCompareVersions(t *testing.T) {
	cases := []struct {
		a, b string
		want int
	}{
		{"1.0", "1.0", 0},
		{"1.0", "1.0.0", 0},
		{"1.2", "1.10", -1},
		{"2.0", "1.99.99", 1},
		{"1.0b1", "1.0", -1},
		{"1.0", "1.0b1", 1},
		{"1.0.1", "1.0", 1},
		{"120.0.6099.71", "120.0.6099.109", -1},
		{"5.17.11 (31580)", "5.17.11 (31580)", 0},
		{"1.0a", "1.0b", -1},
		{"18446744073709551615", "1", 1},
	}
	for _, c := range cases {
		if got := compareVersions(c.a, c.b); got != c.want {
			t.Errorf("compareVersions(%q, %q) = %d, want %d", c.a, c.b, got, c.want)
		}
		if got := compareVersions(c.b, c.a); got != -c.want {
			t.Errorf("compareVersions(%q, %q) = %d, want %d", c.b, c.a, got, -c.want)
		}
	}
}
