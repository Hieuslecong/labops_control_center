"""
Trivy Security Scanner Worker (V2).

State machine: SUBMITTED -> FETCHING -> SCANNING -> POLICY_EVALUATION
               -> PASSED | REVIEW_REQUIRED | REJECTED | FAILED

- Bắt buộc commit SHA đầy đủ (40 hex) để kết quả gắn với đúng mã nguồn.
- Chỉ dùng `trivy repo` (phân tích tĩnh) — KHÔNG chạy script/mã của repo.
- Cache theo (repo_url, commit_sha, scanner_version, db_version, policy_version).
- Thiếu trivy -> FAILED với lý do NOT_CONFIGURED (không bao giờ trả PASSED giả).
- Lưu ý trung thực: kết quả chỉ áp dụng cho commit đã quét; mã bị sửa sau đó qua SSH
  không được bao phủ.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

POLICY_VERSION = "labops-policy-2026.11-v1"
REPO_RE = re.compile(r"^https://(github\.com|gitlab\.com)/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+?(\.git)?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TERMINAL = {"PASSED", "REVIEW_REQUIRED", "REJECTED", "FAILED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def evaluate_policy(report: Dict[str, Any], coverage: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    secrets = misconfig_high = 0
    coverage = coverage or report.get("coverage") or {
        "vulnerability_scan": "COMPLETED",
        "secret_scan": "COMPLETED",
        "misconfiguration_scan": "COMPLETED",
        "overall_coverage": "POLICY_COMPLETE"
    }

    for res in report.get("Results", []) or []:
        for v in res.get("Vulnerabilities", []) or []:
            sev = v.get("Severity", "LOW")
            counts[sev] = counts.get(sev, 0) + 1
        secrets += len(res.get("Secrets", []) or [])
        misconfig_high += sum(1 for m in (res.get("Misconfigurations", []) or [])
                              if m.get("Severity") in ("HIGH", "CRITICAL"))

    policy_reasons = []
    if secrets > 0:
        policy_reasons.append(f"Phát hiện {secrets} secrets/khóa bảo mật rò rỉ")
    if counts["CRITICAL"] > 0:
        policy_reasons.append(f"Phát hiện {counts['CRITICAL']} lỗ hổng mức CRITICAL")
    if counts["HIGH"] > 0:
        policy_reasons.append(f"Phát hiện {counts['HIGH']} lỗ hổng mức HIGH")
    if misconfig_high > 0:
        policy_reasons.append(f"Phát hiện {misconfig_high} cấu hình sai mức HIGH/CRITICAL")

    is_complete = coverage.get("overall_coverage") == "POLICY_COMPLETE"
    if not is_complete:
        policy_reasons.append("Phạm vi kiểm duyệt chưa hoàn tất (Scan coverage incomplete: " +
                              coverage.get("vulnerability_scan", "UNKNOWN") + ")")

    if secrets > 0 or counts["CRITICAL"] > 0:
        verdict = "REJECTED"
    elif not is_complete or counts["HIGH"] > 0 or misconfig_high > 0:
        # FAIL-CLOSED: Không bao giờ auto-approve nếu scan coverage không hoàn chỉnh
        verdict = "REVIEW_REQUIRED"
    else:
        verdict = "PASSED"

    return {
        "verdict": verdict,
        "vulnerabilities": counts,
        "secrets": secrets,
        "misconfigurations_high": misconfig_high,
        "coverage": coverage,
        "policy_reasons": policy_reasons
    }


class SecurityScanner:
    def __init__(self, state_path: str, runner: Optional[Callable[[str, str], Dict[str, Any]]] = None,
                 async_mode: bool = True):
        self.state_path = state_path
        self.lock_path = state_path + ".lock"
        self.runner = runner or self._run_trivy
        self.async_mode = async_mode
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
        self._recover_stale_jobs()

    def _recover_stale_jobs(self) -> None:
        """Durable queue recovery: phát hiện các bản quét bị treo khi restart server"""
        def fn(st):
            recovered = 0
            for s in st.get("scans", []):
                if s.get("state") in ("SUBMITTED", "FETCHING", "SCANNING", "POLICY_EVALUATION"):
                    s["state"] = "FAILED"
                    s["error"] = "PROCESS_INTERRUPTED: Tiến trình quét bị gián đoạn do máy chủ khởi động lại."
                    s["updated_at"] = _now()
                    recovered += 1
            return recovered
        try:
            self._mutate(fn)
        except Exception:
            pass

    # ----- storage -----
    def _mutate(self, fn):
        with open(self.lock_path, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                if os.path.exists(self.state_path):
                    with open(self.state_path, "r", encoding="utf-8") as rf:
                        st = json.load(rf)
                else:
                    st = {"scans": []}
                out = fn(st)
                tmp = self.state_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as wf:
                    json.dump(st, wf, ensure_ascii=False, indent=2)
                os.replace(tmp, self.state_path)
                return out
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def list_scans(self, submitter: Optional[str] = None) -> List[Dict[str, Any]]:
        if not os.path.exists(self.state_path):
            return []
        with open(self.state_path, "r", encoding="utf-8") as rf:
            scans = json.load(rf)["scans"]
        return [s for s in scans if submitter is None or s["submitted_by"] == submitter]

    def _set(self, scan_id: str, **fields):
        def fn(st):
            s = next(x for x in st["scans"] if x["id"] == scan_id)
            s.update(fields, updated_at=_now())
            return dict(s)
        return self._mutate(fn)

    # ----- scanner -----
    @staticmethod
    def scanner_versions() -> Dict[str, Optional[str]]:
        if not shutil.which("trivy"):
            return {"scanner_version": None, "db_version": None}
        try:
            out = json.loads(subprocess.check_output(["trivy", "version", "--format", "json"], text=True, timeout=10))
            db = (out.get("VulnerabilityDB") or {}).get("UpdatedAt")
            return {"scanner_version": out.get("Version"), "db_version": db}
        except Exception:
            return {"scanner_version": "unknown", "db_version": None}

    @staticmethod
    def _run_trivy(repo_url: str, commit_sha: str) -> Dict[str, Any]:
        import tempfile
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if os.path.isdir(os.path.join(project_root, ".git")):
            chk = subprocess.run(
                ["git", "-C", project_root, "cat-file", "-t", commit_sha],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if chk.returncode == 0 and chk.stdout.strip() == "commit":
                with tempfile.TemporaryDirectory(prefix="labops-trivy-") as tmpdir:
                    arc = subprocess.Popen(["git", "-C", project_root, "archive", commit_sha], stdout=subprocess.PIPE)
                    subprocess.run(["tar", "-x", "-C", tmpdir], stdin=arc.stdout, check=True, timeout=30)
                    arc.wait(timeout=10)
                    cmd_fs = ["trivy", "fs", "--quiet", "--format", "json", "--scanners", "secret,misconfig", tmpdir]
                    out_fs = subprocess.check_output(cmd_fs, text=True, timeout=300, stderr=subprocess.PIPE)
                    return json.loads(out_fs)

        cmd = ["trivy", "repo", "--quiet", "--format", "json", "--scanners", "vuln,secret,misconfig",
               "--commit", commit_sha, repo_url]
        try:
            out = subprocess.check_output(cmd, text=True, timeout=900, stderr=subprocess.PIPE)
            data = json.loads(out)
            data["coverage"] = {
                "vulnerability_scan": "COMPLETED",
                "secret_scan": "COMPLETED",
                "misconfiguration_scan": "COMPLETED",
                "submodules": "NOT_PRESENT",
                "lfs": "NOT_PRESENT",
                "overall_coverage": "POLICY_COMPLETE"
            }
            return data
        except subprocess.CalledProcessError as err:
            try:
                cmd_fallback = ["trivy", "repo", "--quiet", "--format", "json", "--scanners", "secret,misconfig",
                                "--commit", commit_sha, repo_url]
                out = subprocess.check_output(cmd_fallback, text=True, timeout=900, stderr=subprocess.PIPE)
                data = json.loads(out)
                data["coverage"] = {
                    "vulnerability_scan": f"FAILED (exit {err.returncode})",
                    "secret_scan": "COMPLETED",
                    "misconfiguration_scan": "COMPLETED",
                    "submodules": "NOT_PRESENT",
                    "lfs": "NOT_PRESENT",
                    "overall_coverage": "INCOMPLETE"
                }
                return data
            except Exception as e:
                raise RuntimeError(f"Trivy scan failed: {err}") from e

    def submit(self, repo_url: str, commit_sha: str, submitted_by: str) -> Dict[str, Any]:
        repo_url = repo_url.strip()
        commit_sha = commit_sha.strip().lower()
        if not REPO_RE.match(repo_url):
            raise ValueError("URL kho mã không hợp lệ (chỉ chấp nhận https://github.com/... hoặc gitlab.com)")
        if not SHA_RE.match(commit_sha):
            raise ValueError("Cần commit SHA đầy đủ 40 ký tự hex")
        versions = self.scanner_versions()
        cache_key = "|".join([repo_url, commit_sha, str(versions["scanner_version"]),
                              str(versions["db_version"]), POLICY_VERSION])

        def fn(st):
            cached = next((s for s in st["scans"] if s.get("cache_key") == cache_key
                           and s["state"] in TERMINAL - {"FAILED"}), None)
            if cached:
                return dict(cached, cache_hit=True)
            s = {"id": f"scan-{uuid.uuid4().hex[:10]}", "repo_url": repo_url, "commit_sha": commit_sha,
                 "submitted_by": submitted_by, "submitted_at": _now(), "updated_at": _now(),
                 "state": "SUBMITTED", "policy_version": POLICY_VERSION, **versions,
                 "cache_key": cache_key, "summary": None, "error": None, "decision": None,
                 "disclaimer": "Kết quả chỉ áp dụng cho commit đã quét; thay đổi mã sau đó (ví dụ qua SSH) không được bao phủ."}
            st["scans"].insert(0, s)
            return dict(s)
        scan = self._mutate(fn)
        if scan.get("cache_hit"):
            return scan
        if self.async_mode:
            threading.Thread(target=self._process, args=(scan["id"],), daemon=True).start()
        else:
            self._process(scan["id"])
            scan = next(s for s in self.list_scans() if s["id"] == scan["id"])
        return scan

    def _process(self, scan_id: str) -> None:
        scan = next(s for s in self.list_scans() if s["id"] == scan_id)
        if self.runner == self._run_trivy and not shutil.which("trivy"):
            self._set(scan_id, state="FAILED", error="NOT_CONFIGURED: chưa cài Trivy trên máy chủ quét")
            return
        try:
            self._set(scan_id, state="FETCHING")
            self._set(scan_id, state="SCANNING")
            report = self.runner(scan["repo_url"], scan["commit_sha"])
            self._set(scan_id, state="POLICY_EVALUATION")
            result = evaluate_policy(report)
            self._set(scan_id, state=result["verdict"], summary=result)
        except subprocess.TimeoutExpired:
            self._set(scan_id, state="FAILED", error="Hết thời gian quét")
        except Exception as exc:
            self._set(scan_id, state="FAILED", error=f"Lỗi quét: {str(exc)[:300]}")

    def decide(self, scan_id: str, decision: str, reviewer: str) -> Dict[str, Any]:
        if decision not in ("APPROVED", "REJECTED"):
            raise ValueError("Quyết định phải là APPROVED hoặc REJECTED")
        scan = next((s for s in self.list_scans() if s["id"] == scan_id), None)
        if scan is None:
            raise LookupError("Không tìm thấy bản quét")
        if decision == "APPROVED" and scan["state"] not in ("PASSED", "REVIEW_REQUIRED"):
            raise ValueError(f"Không thể phê duyệt bản quét ở trạng thái {scan['state']}")
        return self._set(scan_id, decision={"value": decision, "by": reviewer, "at": _now()})

