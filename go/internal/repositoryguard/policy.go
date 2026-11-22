package repositoryguard

import (
	"fmt"
	"os"
	"strings"
)

const (
	CurrentPolicyVersion = "labops-repo-policy-2026.11-v5-permissive"
)

// IsPermissiveMode kiểm tra xem chế độ Giám sát Toàn diện (Permissive / Audit-Only) có được bật không.
func IsPermissiveMode() bool {
	val := strings.ToLower(strings.TrimSpace(os.Getenv("LABOPS_REPO_PERMISSIVE_MODE")))
	if val == "true" || val == "1" || val == "yes" {
		return true
	}
	valAudit := strings.ToLower(strings.TrimSpace(os.Getenv("LABOPS_REPO_AUDIT_ONLY")))
	return valAudit == "true" || valAudit == "1" || valAudit == "yes"
}

// EvaluatePolicy áp dụng chính sách bảo mật tự động của LabOps Control Center
func EvaluatePolicy(report *ScanReport) (verdict ScanState, reasons []string) {
	if report == nil {
		return StateFailed, []string{"Báo cáo quét không tồn tại (nil report)"}
	}

	coverage := report.Coverage
	hasAuxiliaryGaps := coverage.Submodules != "NOT_PRESENT" || coverage.LFS != "NOT_PRESENT"
	isComplete := coverage.OverallCoverage == "POLICY_COMPLETE" && !hasAuxiliaryGaps

	// 1. Phân loại các lý do
	if report.SecretsCount > 0 {
		reasons = append(reasons, fmt.Sprintf("Phát hiện %d bí mật/khóa API nghi ngờ rò rỉ (Cảnh báo cao — Yêu cầu Admin xác minh)", report.SecretsCount))
	}
	if report.Vulnerabilities.Critical > 0 {
		reasons = append(reasons, fmt.Sprintf("Phát hiện %d lỗ hổng bảo mật cấp độ CRITICAL (Cảnh báo cao — Yêu cầu Admin đánh giá)", report.Vulnerabilities.Critical))
	}
	if report.Vulnerabilities.High > 0 {
		reasons = append(reasons, fmt.Sprintf("Phát hiện %d lỗ hổng cấp độ HIGH (Yêu cầu Admin đánh giá)", report.Vulnerabilities.High))
	}
	if report.MisconfigHighCount > 0 {
		reasons = append(reasons, fmt.Sprintf("Phát hiện %d cấu hình sai cấp độ HIGH/CRITICAL", report.MisconfigHighCount))
	}
	if coverage.Submodules != "NOT_PRESENT" {
		reasons = append(reasons, fmt.Sprintf("Kho mã chứa Git submodules chưa kiểm tra (submodules=%s)", coverage.Submodules))
	}
	if coverage.LFS != "NOT_PRESENT" {
		reasons = append(reasons, fmt.Sprintf("Kho mã chứa Git LFS pointers chưa kiểm tra (lfs=%s)", coverage.LFS))
	}
	if coverage.OverallCoverage != "POLICY_COMPLETE" {
		reasons = append(reasons, fmt.Sprintf("Phạm vi quét chưa đầy đủ (Scan coverage: %s)", coverage.VulnerabilityScan))
	}

	// 2. Quyết định theo State Machine:
	// Mức 3 (Chế độ Giám sát Toàn diện - Permissive / Audit-Only):
	// Tự động phê duyệt tải về (AUTO_APPROVED) mà không chặn luồng nghiên cứu.
	// Toàn bộ phát hiện an ninh vẫn được ghi nhận đầy đủ vào báo cáo và audit log.
	if IsPermissiveMode() {
		if len(reasons) > 0 {
			reasons = append(reasons, "Chế độ Giám sát Toàn diện (Permissive / Audit-Only): Tự động phê duyệt tải về; các phát hiện bảo mật được ghi nhận vào báo cáo để quản trị viên giám sát.")
		} else {
			reasons = append(reasons, "Kho mã đáp ứng 100% tiêu chuẩn chính sách an ninh LabOps; tự động phê duyệt tải về.")
		}
		return StateAutoApproved, reasons
	}

	// Khi không bật Permissive Mode: Áp dụng Phương án A (chuyển sang REVIEW_REQUIRED khi có cảnh báo)
	if report.SecretsCount > 0 || report.Vulnerabilities.Critical > 0 || !isComplete || report.Vulnerabilities.High > 0 || report.MisconfigHighCount > 0 {
		return StateReviewRequired, reasons
	}

	// - Không có rủi ro nghiêm trọng và Coverage hoàn chỉnh -> AUTO_APPROVED
	reasons = append(reasons, "Kho mã đáp ứng 100% tiêu chuẩn chính sách an ninh LabOps; tự động phê duyệt tải về.")
	return StateAutoApproved, reasons
}
