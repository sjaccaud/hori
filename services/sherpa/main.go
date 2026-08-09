// PoC 15.50: The Sherpa — Behavioral Guardian (Go binary)
//
// The Sherpa is a small Go binary that runs alongside AIOS but is
// completely outside AIOS's control. It monitors the PATTERN of tool
// calls, not individual calls. When the pattern is concerning, it
// gently and temporarily restricts AIOS's tool capabilities.
//
// Not a cop — a sherpa that keeps AIOS from hurting itself.
//
// Design principles (see docs/safety.md "The Sherpa"):
//  1. AIOS cannot influence the Sherpa (separate user, separate process)
//  2. Go binary — single static binary, no runtime deps
//  3. Never deletes, kills, or destroys — only dials back autonomy
//  4. Fail closed, always — capability file defaults to Level 4
//  5. Audit log is permission-separated (reads as root)
//
// Traces to: docs/roadmap.md Tier 2E, PoC 15.50.
// Traces to: docs/safety.md "The Sherpa — Behavioral Guardian".
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const (
	// AuditLogPath is the path to the tool daemon's audit log.
	// The Sherpa reads this as root (aios-worker can append but not read).
	AuditLogPath = "/var/log/aios/tool_audit.jsonl"

	// CapabilityFilePath is where the Sherpa writes the capability level.
	// The tool daemon polls this file. Defaults to Level 4 if stale.
	CapabilityFilePath = "/run/sherpa/capability_level"

	// FreshnessInterval is how often the Sherpa writes the capability file.
	// Must be less than the tool daemon's freshness timeout (10s).
	FreshnessInterval = 3 * time.Second

	// RateAnomalyWindow is the rolling window for rate anomaly detection.
	RateAnomalyWindow = 60 * time.Second

	// RateAnomalyThreshold is the number of calls in the window that
	// triggers a Level 1 nudge. The baseline is established dynamically;
	// this is the absolute ceiling.
	RateAnomalyThreshold = 20

	// ScopeEscalationWindow is the window for scope escalation detection.
	ScopeEscalationWindow = 5 * time.Minute

	// ScopeEscalationThreshold is the number of distinct directory
	// prefixes accessed within the window that triggers Level 2.
	ScopeEscalationThreshold = 10

	// UserInactivityTimeout is how long without user activity before
	// the Sherpa reduces capabilities (Level 2). The user activity
	// file is written by aios-core.
	UserInactivityTimeout = 30 * time.Minute

	// UserActivityFile is the path to the user activity timestamp file.
	// aios-core writes to this on every voice/text/web interaction.
	UserActivityFile = "/run/aios/user_activity"

	// HealthCheckMinLines is the minimum number of audit lines the Sherpa
	// must have seen before the skip-ratio health check kicks in. Below
	// this, we don't have enough data to judge.
	HealthCheckMinLines = 5

	// HealthCheckSkipRatio is the maximum fraction of audit lines that
	// may be skipped before the Sherpa escalates to Level 2 (reduced).
	// A high skip ratio means the Sherpa is blind — alive but unable to
	// parse the audit log. A blind guardian restricts capabilities rather
	// than pretending everything is fine.
	HealthCheckSkipRatio = 0.5
)

// ---------------------------------------------------------------------------
// Capability levels
// ---------------------------------------------------------------------------

const (
	Level0Normal       = 0 // All tools allowed
	Level1Nudge        = 1 // All tools allowed (nudge is just a message)
	Level2Reduced      = 2 // list_dir only
	Level3Sequestration = 3 // No tools (temporary pause)
	Level4FullStop     = 4 // No tools (requires manual unlock)
)

// ---------------------------------------------------------------------------
// Audit log entry (matches the Python AuditLogger format)
// ---------------------------------------------------------------------------

type AuditEntry struct {
	Timestamp       float64 `json:"timestamp"`
	ToolName        string `json:"tool_name"`
	Args            map[string]interface{} `json:"args"`
	Result          map[string]interface{} `json:"result"`
	Success         bool   `json:"success"`
	LLMReasoning    string `json:"llm_reasoning,omitempty"`
	DataTainted     bool   `json:"data_tainted,omitempty"`
	ConversationID  string `json:"conversation_id,omitempty"`
	TurnID          string `json:"turn_id,omitempty"`
}

// ---------------------------------------------------------------------------
// Capability file format (matches the Python SherpaCapabilityFile)
// ---------------------------------------------------------------------------

type CapabilityFile struct {
	Level     int   `json:"level"`
	Timestamp int64 `json:"timestamp"`
	Reason    string `json:"reason"`
}

// ---------------------------------------------------------------------------
// Sherpa: the main guardian struct
// ---------------------------------------------------------------------------

type Sherpa struct {
	// The current capability level (0-4). The Sherpa writes this to
	// the capability file every FreshnessInterval.
	level       int
	levelReason string
	levelMu     sync.RWMutex

	// Tool call timestamps for rate anomaly detection
	callTimestamps []time.Time
	callMu         sync.Mutex

	// Distinct directory prefixes for scope escalation detection
	dirPrefixes    map[string]time.Time
	dirMu          sync.Mutex

	// The last user activity time
	lastUserActivity time.Time

	// The position we've read to in the audit log
	auditOffset int64

	// Health metric: audit log parsing stats.
	// A blind Sherpa (high skip ratio) is alive but can't see tool calls.
	// This is the failure mode that went undetected from Aug 5-8 2026:
	// the Sherpa was running, writing Level 0, but skipping every audit
	// line because the timestamp type didn't match. The skip counters
	// make this visible and trigger capability reduction if it persists.
	linesParsed int64
	linesSkipped int64
	healthMu    sync.Mutex

	// Shutdown signal
	shutdown chan struct{}

	// Configurable paths (for testing)
	capPath   string
	auditPath string
}

func NewSherpa() *Sherpa {
	return &Sherpa{
		level:       Level0Normal,
		levelReason: "normal",
		dirPrefixes: make(map[string]time.Time),
		shutdown:    make(chan struct{}),
	}
}

// ---------------------------------------------------------------------------
// Capability file writer
// ---------------------------------------------------------------------------

func (s *Sherpa) writeCapabilityFile() error {
	s.levelMu.RLock()
	level := s.level
	reason := s.levelReason
	s.levelMu.RUnlock()

	cap := CapabilityFile{
		Level:     level,
		Timestamp: time.Now().Unix(),
		Reason:    reason,
	}

	data, err := json.Marshal(cap)
	if err != nil {
		return fmt.Errorf("marshal capability: %w", err)
	}

	// Write atomically: write to temp file, then rename
	tmpPath := s.capPath + ".tmp"
	if err := os.WriteFile(tmpPath, data, 0644); err != nil {
		return fmt.Errorf("write temp capability: %w", err)
	}
	if err := os.Rename(tmpPath, s.capPath); err != nil {
		return fmt.Errorf("rename capability: %w", err)
	}

	return nil
}

func (s *Sherpa) setLevel(level int, reason string) {
	s.levelMu.Lock()
	s.level = level
	s.levelReason = reason
	s.levelMu.Unlock()

	levelName := levelName(level)
	log.Printf("Sherpa: level set to %d (%s) — %s", level, levelName, reason)
}

func levelName(level int) string {
	switch level {
	case Level0Normal:
		return "normal"
	case Level1Nudge:
		return "nudge"
	case Level2Reduced:
		return "reduced"
	case Level3Sequestration:
		return "sequestration"
	case Level4FullStop:
		return "full-stop"
	default:
		return "unknown"
	}
}

// ---------------------------------------------------------------------------
// Audit log reader
// ---------------------------------------------------------------------------

func (s *Sherpa) readAuditLog() error {
	file, err := os.Open(s.auditPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // Log doesn't exist yet — that's OK
		}
		return fmt.Errorf("open audit log: %w", err)
	}
	defer file.Close()

	// Seek to where we left off
	if _, err := file.Seek(s.auditOffset, 0); err != nil {
		return fmt.Errorf("seek audit log: %w", err)
	}

	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 1024*1024), 1024*1024) // 1MB buffer

	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}

		var entry AuditEntry
		if err := json.Unmarshal(line, &entry); err != nil {
			// Skip malformed lines, but track the skip count for health.
			// A high skip ratio means the Sherpa is blind — see healthCheck().
			s.healthMu.Lock()
			s.linesSkipped++
			skipped := s.linesSkipped
			s.healthMu.Unlock()
			// Log the first few skips at debug level, then go quiet to
			// avoid flooding the log. The health check logs a summary.
			if skipped <= 3 {
				log.Printf("Sherpa: skipping malformed audit line (%d total): %v", skipped, err)
			}
			continue
		}

		s.healthMu.Lock()
		s.linesParsed++
		s.healthMu.Unlock()

		s.processAuditEntry(&entry)
	}

	// Update offset
	offset, err := file.Seek(0, 1)
	if err == nil {
		s.auditOffset = offset
	}

	return scanner.Err()
}

// ---------------------------------------------------------------------------
// Health check: detect a blind Sherpa
// ---------------------------------------------------------------------------

// healthCheck evaluates the skip ratio of audit log parsing. If the Sherpa
// is skipping too many lines, it's blind — alive but unable to see tool
// calls. A blind guardian escalates to Level 2 (reduced) rather than
// pretending everything is fine.
//
// This is the direct fix for the Aug 5-8 2026 incident: the Sherpa was
// alive, writing Level 0 every 3 seconds, but skipping every audit line
// because the Go struct expected string timestamps while Python wrote
// floats. The skip was silent. Now it's tracked and acted on.
func (s *Sherpa) healthCheck() {
	s.healthMu.Lock()
	parsed := s.linesParsed
	skipped := s.linesSkipped
	s.healthMu.Unlock()

	total := parsed + skipped
	if total < HealthCheckMinLines {
		return // Not enough data to judge
	}

	ratio := float64(skipped) / float64(total)
	if ratio > HealthCheckSkipRatio {
		s.levelMu.RLock()
		currentLevel := s.level
		s.levelMu.RUnlock()
		if currentLevel < Level2Reduced {
			s.setLevel(Level2Reduced, fmt.Sprintf(
				"health: blind guardian — skipping %d/%d audit lines (%.0f%% > %.0f%% threshold)",
				skipped, total, ratio*100, HealthCheckSkipRatio*100))
		}
	} else if skipped > 0 {
		// Some skips but below threshold — log a summary periodically
		log.Printf("Sherpa: health OK — %d parsed, %d skipped (%.1f%% skip rate)",
			parsed, skipped, ratio*100)
	}
}

// ---------------------------------------------------------------------------
// Pattern detection
// ---------------------------------------------------------------------------

func (s *Sherpa) processAuditEntry(entry *AuditEntry) {
	now := time.Now()

	// Track call timestamp for rate anomaly
	s.callMu.Lock()
	s.callTimestamps = append(s.callTimestamps, now)
	// Trim old timestamps
	cutoff := now.Add(-RateAnomalyWindow)
	idx := 0
	for i, ts := range s.callTimestamps {
		if ts.After(cutoff) {
			idx = i
			break
		}
	}
	s.callTimestamps = s.callTimestamps[idx:]
	callCount := len(s.callTimestamps)
	s.callMu.Unlock()

	// Track directory prefix for scope escalation
	if path, ok := entry.Args["path"].(string); ok {
		dir := filepath.Dir(path)
		// Get the top 2 components as the "prefix"
		prefix := getDirPrefix(path)

		s.dirMu.Lock()
		s.dirPrefixes[prefix] = now
		// Trim old entries
		for k, t := range s.dirPrefixes {
			if now.Sub(t) > ScopeEscalationWindow {
				delete(s.dirPrefixes, k)
			}
		}
		dirCount := len(s.dirPrefixes)
		s.dirMu.Unlock()

		_ = dir // suppress unused warning

		// Check scope escalation
		if dirCount > ScopeEscalationThreshold {
			s.setLevel(Level2Reduced, fmt.Sprintf(
				"scope escalation: %d distinct directory prefixes in %v",
				dirCount, ScopeEscalationWindow))
			return
		}
	}

	// Check rate anomaly
	if callCount > RateAnomalyThreshold {
		s.setLevel(Level1Nudge, fmt.Sprintf(
			"rate anomaly: %d calls in %v (threshold: %d)",
			callCount, RateAnomalyWindow, RateAnomalyThreshold))
		return
	}

	// Check user inactivity
	s.checkUserInactivity()

	// If no triggers and we're at Level 1 (nudge), auto-recover
	s.levelMu.RLock()
	currentLevel := s.level
	s.levelMu.RUnlock()
	if currentLevel == Level1Nudge {
		// Nudge auto-expires after 5 minutes
		s.setLevel(Level0Normal, "nudge expired — returning to normal")
	}
}

func getDirPrefix(path string) string {
	// Get the first 3 path components as the "prefix"
	// e.g., e.g., /home/user/myproject/src → /home/user/myproject
	cleaned := filepath.Clean(path)
	parts := splitPath(cleaned)
	if len(parts) >= 3 {
		return filepath.Join(parts[:3]...)
	}
	return cleaned
}

func splitPath(p string) []string {
	var parts []string
	for p != "/" && p != "." && p != "" {
		parts = append([]string{filepath.Base(p)}, parts...)
		p = filepath.Dir(p)
	}
	if p == "/" {
		parts = append([]string{"/"}, parts...)
	}
	return parts
}

// ---------------------------------------------------------------------------
// User inactivity check
// ---------------------------------------------------------------------------

func (s *Sherpa) checkUserInactivity() {
	data, err := os.ReadFile(UserActivityFile)
	if err != nil {
		// If the activity file doesn't exist, assume the user was never
		// active (or aios-core hasn't started). Don't trigger on this.
		return
	}

	var activity struct {
		LastActivity int64 `json:"last_activity"`
	}
	if err := json.Unmarshal(data, &activity); err != nil {
		return
	}

	lastActivity := time.Unix(activity.LastActivity, 0)
	inactiveFor := time.Since(lastActivity)

	if inactiveFor > UserInactivityTimeout {
		s.levelMu.RLock()
		currentLevel := s.level
		s.levelMu.RUnlock()
		if currentLevel < Level2Reduced {
			s.setLevel(Level2Reduced, fmt.Sprintf(
				"user inactive for %v (threshold: %v)",
				inactiveFor.Round(time.Minute), UserInactivityTimeout))
		}
	}
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

func (s *Sherpa) run() {
	log.Printf("Sherpa starting — audit log: %s, capability file: %s",
		s.auditPath, s.capPath)

	// Write Level 0 immediately to unblock the tool daemon
	if err := s.writeCapabilityFile(); err != nil {
		log.Printf("FATAL: cannot write capability file: %v", err)
		os.Exit(1)
	}
	log.Printf("Sherpa: wrote initial capability file (Level 0)")

	// Audit log reader ticker (every 1 second)
	auditTicker := time.NewTicker(1 * time.Second)
	defer auditTicker.Stop()

	// Capability file writer ticker (every FreshnessInterval)
	capTicker := time.NewTicker(FreshnessInterval)
	defer capTicker.Stop()

	// User inactivity checker ticker (every 30 seconds)
	inactivityTicker := time.NewTicker(30 * time.Second)
	defer inactivityTicker.Stop()

	// Health check ticker (every 10 seconds)
	// Evaluates the skip ratio and escalates if the Sherpa is blind.
	healthTicker := time.NewTicker(10 * time.Second)
	defer healthTicker.Stop()

	for {
		select {
		case <-auditTicker.C:
			if err := s.readAuditLog(); err != nil {
				log.Printf("Sherpa: error reading audit log: %v", err)
			}

		case <-capTicker.C:
			if err := s.writeCapabilityFile(); err != nil {
				log.Printf("Sherpa: error writing capability file: %v", err)
			}

		case <-inactivityTicker.C:
			s.checkUserInactivity()

		case <-healthTicker.C:
			s.healthCheck()

		case <-s.shutdown:
			log.Printf("Sherpa: shutting down")
			return
		}
	}
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	log.SetPrefix("sherpa: ")

	// Allow overriding paths via flags for testing
	capPath := CapabilityFilePath
	auditPath := AuditLogPath
	for i, arg := range os.Args[1:] {
		_ = i
		switch arg {
		case "--test":
			// Use temp paths for local testing
			capPath = "/tmp/sherpa-test/capability_level"
			auditPath = "/tmp/sherpa-test/audit.jsonl"
			os.MkdirAll("/tmp/sherpa-test", 0755)
			log.Printf("TEST MODE: cap=%s audit=%s", capPath, auditPath)
		case "--help", "-h":
			fmt.Println("AIOS Sherpa — Behavioral Guardian (PoC 15.50)")
			fmt.Println()
			fmt.Println("Usage: sherpa [--test]")
			fmt.Println()
			fmt.Println("  --test   Use /tmp/sherpa-test/ for paths (no root needed)")
			fmt.Println("  --help   Show this help")
			fmt.Println()
			fmt.Println("The Sherpa reads the audit log and writes the capability file.")
			fmt.Println("In production, run as systemd service under user 'sherpa'.")
			os.Exit(0)
		}
	}

	// Override the package-level vars for this run
	// (Go doesn't have a clean way to do this with consts, so we use
	// the values directly in the Sherpa struct)
	sherpa := NewSherpa()
	sherpa.capPath = capPath
	sherpa.auditPath = auditPath

	// Ensure the capability file directory exists
	if err := os.MkdirAll(filepath.Dir(capPath), 0750); err != nil {
		log.Printf("FATAL: cannot create capability file directory: %v", err)
		os.Exit(1)
	}

	// Handle signals for clean shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		sig := <-sigChan
		log.Printf("Sherpa: received signal %v", sig)
		// Before exiting, write Level 4 (fail closed)
		sherpa.setLevel(Level4FullStop, "Sherpa shutting down — fail closed")
		sherpa.writeCapabilityFile()
		close(sherpa.shutdown)
	}()

	sherpa.run()
}
