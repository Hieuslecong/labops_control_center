package repositoryguard

import (
	"fmt"
)

const (
	CurrentPolicyVersion = "labops-repo-policy-2026.11-v2"
)

// EvaluatePolicy áp dụng chính sách bảo mật tự động của LabOps Control Center
func EvaluatePolicy(report *ScanReport) (verdict ScanState, reasons []string) {
	if report == nil {
		return StateFailed, []string{"Báo cáo quét không tồn tại (nil report)"}
	}

	coverage := report.Coverage
	isComplete := coverage.OverallCoverage == "POLICY_COMPLETE"

	// 1. Phân loại các lý do
	if report.SecretsCount > 0 {
		reasons = append(reasons, fmt.Sprintf("Phát hiện %d bí mật/khóa API bị rò rỉ (Chặn nghiêm ngặt)", report.SecretsCount))
	}
	if report.Vulnerabilities.Critical > 0 {
		reasons = append(reasons, fmt.Sprintf("Phát hiện %d lỗ hổng bảo mật cấp độ CRITICAL", report.Vulnerabilities.Critical))
	}
	if report.Vulnerabilities.High > 0 {
		reasons = append(reasons, fmt.Sprintf("Phát hiện %d lỗ hổng cấp độ HIGH (Yêu cầu Admin đánh giá)", report.Vulnerabilities.High))
	}
	if report.MisconfigHighCount > 0 {
		reasons = append(reasons, fmt.Sprintf("Phát hiện %d cấu hình sai cấp độ HIGH/CRITICAL", report.MisconfigHighCount))
	}
	if !isComplete {
		reasons = append(reasons, fmt.Sprintf("Phạm vi quét chưa đầy đủ (Scan coverage: %s)", coverage.VulnerabilityScan))
	}

	// 2. Quyết định theo State Machine:
	// - Có secrets hoặc Critical -> REJECTED
	if report.SecretsCount > 0 || report.Vulnerabilities.Critical > 0 {
		return StateRejected, reasons
	}

	// - Có High vuln, High misconfig, hoặc Coverage thiếu -> REVIEW_REQUIRED (Fail-closed)
	if !isComplete || report.Vulnerabilities.High > 0 || report.MisconfigHighCount > 0 {
		return StateReviewRequired, reasons
	}

	// - Không có rủi ro nghiêm trọng và Coverage hoàn chỉnh -> AUTO_APPROVED
	reasons = append(reasons, "Kho mã đáp ứng 100% tiêu chuẩn chính sách an ninh LabOps; tự động phê duyệt tải về.")
	return StateAutoApproved, reasons
}
