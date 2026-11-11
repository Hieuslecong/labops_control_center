package repositoryguard

import (
	"time"
)

type ScanState string

const (
	StateSubmitted        ScanState = "SUBMITTED"
	StateValidating       ScanState = "VALIDATING"
	StateResolvingCommit  ScanState = "RESOLVING_COMMIT"
	StateCheckingCache    ScanState = "CHECKING_CACHE"
	StateFetching         ScanState = "FETCHING"
	StateScanning         ScanState = "SCANNING"
	StatePolicyEvaluation ScanState = "POLICY_EVALUATION"
	StateAutoApproved     ScanState = "AUTO_APPROVED"
	StateReviewRequired   ScanState = "REVIEW_REQUIRED"
	StateManuallyApproved ScanState = "MANUALLY_APPROVED"
	StateRejected         ScanState = "REJECTED"
	StateFailed           ScanState = "FAILED"
	StatePublishing       ScanState = "PUBLISHING"
	StateDelivered        ScanState = "DELIVERED"
)

type ScanCoverage struct {
	VulnerabilityScan    string `json:"vulnerability_scan"`
	SecretScan           string `json:"secret_scan"`
	MisconfigurationScan string `json:"misconfiguration_scan"`
	Submodules           string `json:"submodules"`
	LFS                  string `json:"lfs"`
	OverallCoverage      string `json:"overall_coverage"`
}

type VulnerabilitySummary struct {
	Critical int `json:"critical"`
	High     int `json:"high"`
	Medium   int `json:"medium"`
	Low      int `json:"low"`
}

type ScorecardSummary struct {
	Score       float64           `json:"score"`
	Status      string            `json:"status"` // "EVALUATED", "UNKNOWN", "NOT_CONFIGURED"
	Checks      map[string]int    `json:"checks,omitempty"`
	Reasons     []string          `json:"reasons,omitempty"`
	CollectedAt *time.Time        `json:"collected_at,omitempty"`
}

type FindingItem struct {
	ID          string `json:"id"`
	Type        string `json:"type"` // "VULN", "SECRET", "MISCONFIG"
	Severity    string `json:"severity"`
	Title       string `json:"title"`
	TargetFile  string `json:"target_file"`
	MaskedValue string `json:"masked_value,omitempty"`
}

type ScanReport struct {
	Verdict           ScanState            `json:"verdict"`
	Coverage          ScanCoverage         `json:"coverage"`
	Vulnerabilities   VulnerabilitySummary `json:"vulnerabilities"`
	SecretsCount      int                  `json:"secrets_count"`
	MisconfigHighCount int                 `json:"misconfig_high_count"`
	Scorecard         ScorecardSummary     `json:"scorecard"`
	PolicyReasons     []string             `json:"policy_reasons"`
	Findings          []FindingItem        `json:"findings,omitempty"`
	ScannerVersion    string               `json:"scanner_version"`
	DBVersion         string               `json:"db_version,omitempty"`
	ScanDurationMs    int64                `json:"scan_duration_ms"`
}

type DecisionRecord struct {
	Verdict    ScanState `json:"verdict"`
	Reviewer   string    `json:"reviewer"`
	ReviewedAt time.Time `json:"reviewed_at"`
	Reason     string    `json:"reason"`
}

type RepositoryRequest struct {
	ID              string          `json:"id"`
	RepoURL         string          `json:"repo_url"`
	CanonicalRepo   string          `json:"canonical_repo"`
	Owner           string          `json:"owner"`
	RepoName        string          `json:"repo_name"`
	RequestedRef    string          `json:"requested_ref"`
	CommitSHA       string          `json:"commit_sha"`
	Submitter       string          `json:"submitted_by"`
	TargetWorkspace string          `json:"target_workspace,omitempty"`
	TargetServer    string          `json:"target_server,omitempty"`
	SubmittedAt     time.Time       `json:"submitted_at"`
	UpdatedAt       time.Time       `json:"updated_at"`
	State           ScanState       `json:"state"`
	CacheHit        bool            `json:"cache_hit"`
	ArtifactDigest  string          `json:"artifact_digest,omitempty"`
	ArtifactSize    int64           `json:"artifact_size_bytes,omitempty"`
	Report          *ScanReport     `json:"report,omitempty"`
	Decision        *DecisionRecord `json:"decision,omitempty"`
	Error           string          `json:"error,omitempty"`
	PolicyVersion   string          `json:"policy_version"`
}

type DeliverPayload struct {
	RequestID       string `json:"request_id"`
	TargetWorkspace string `json:"target_workspace"`
	TargetDirectory string `json:"target_directory"`
	DeliverAsUser   string `json:"deliver_as_user,omitempty"`
}

type AuditLogEvent struct {
	ID         string    `json:"id"`
	Timestamp  time.Time `json:"timestamp"`
	Actor      string    `json:"actor"`
	Action     string    `json:"action"`
	TargetType string    `json:"target_type"`
	TargetID   string    `json:"target_id"`
	Result     string    `json:"result"`
	Details    string    `json:"details"`
}
