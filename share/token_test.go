package share

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestShareTokenDeterministic(t *testing.T) {
	t.Parallel()

	secret := []byte("12345678901234567890123456789012")
	id := "abc123"

	a := ShareToken(secret, id, DefaultTokenBytes)
	b := ShareToken(secret, id, DefaultTokenBytes)
	if a != b {
		t.Fatalf("ShareToken should be deterministic, got %q and %q", a, b)
	}
	if !ValidateShareToken(secret, id, a, DefaultTokenBytes) {
		t.Fatal("expected token validation to pass")
	}
	if ValidateShareToken(secret, id+"x", a, DefaultTokenBytes) {
		t.Fatal("expected token validation to fail for a different share id")
	}
}

func TestShareTokenCustomBytes(t *testing.T) {
	t.Parallel()

	secret := []byte("12345678901234567890123456789012")
	id := "abc123"

	short := ShareToken(secret, id, 4)
	long := ShareToken(secret, id, 16)
	if len(short) >= len(long) {
		t.Fatalf("expected shorter token with fewer bytes: short=%q long=%q", short, long)
	}
	if !ValidateShareToken(secret, id, short, 4) {
		t.Fatal("expected short token validation to pass")
	}
	if ValidateShareToken(secret, id, short, 16) {
		t.Fatal("expected short token to fail validation with different byte count")
	}
}

func TestNewDaemonRejectsBelowMinTokenBytes(t *testing.T) {
	t.Parallel()

	baseDir := t.TempDir()
	paths := StatePaths{
		BaseDir:      baseDir,
		DBPath:       filepath.Join(baseDir, "shares.db"),
		SecretPath:   filepath.Join(baseDir, "secret"),
		SnapshotsDir: filepath.Join(baseDir, "snapshots"),
		LogsDir:      filepath.Join(baseDir, "logs"),
	}

	_, err := NewDaemon(DaemonConfig{
		Paths:      paths,
		AdminAddr:  "127.0.0.1:0",
		PublicPort: 39124,
		TokenBytes: 1,
	})
	if err == nil {
		t.Fatal("expected NewDaemon to reject TokenBytes=1")
	}
	if !strings.Contains(err.Error(), "minimum") {
		t.Fatalf("error should mention minimum, got: %v", err)
	}
}

func TestNewDaemonAcceptsZeroTokenBytesAsDefault(t *testing.T) {
	t.Parallel()

	baseDir := t.TempDir()
	paths := StatePaths{
		BaseDir:      baseDir,
		DBPath:       filepath.Join(baseDir, "shares.db"),
		SecretPath:   filepath.Join(baseDir, "secret"),
		SnapshotsDir: filepath.Join(baseDir, "snapshots"),
		LogsDir:      filepath.Join(baseDir, "logs"),
	}

	d, err := NewDaemon(DaemonConfig{
		Paths:      paths,
		AdminAddr:  "127.0.0.1:0",
		PublicPort: 39124,
		TokenBytes: 0,
	})
	if err != nil {
		t.Fatalf("NewDaemon with zero TokenBytes: %v", err)
	}
	t.Cleanup(func() { _ = d.Close() })
	if d.cfg.TokenBytes != DefaultTokenBytes {
		t.Fatalf("expected TokenBytes to default to %d, got %d", DefaultTokenBytes, d.cfg.TokenBytes)
	}
}
