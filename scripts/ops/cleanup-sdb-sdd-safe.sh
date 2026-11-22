#!/usr/bin/env bash
# ==============================================================================
# LabOps Safe Disk Cleanup Script (SDB & SDD)
# - Định danh ổ đĩa bằng UUID (không phụ thuộc tên /dev/sdX sau reboot)
# - Mặc định chạy ở chế độ --dry-run (mô phỏng, không xóa dữ liệu)
# - Chỉ xóa theo danh sách allowlist từng thư mục rõ ràng (tuyệt đối không dùng *)
# - 5 lớp bảo vệ tự động: Path/Symlink, Docker Mounts, LabOps Registry, /etc/passwd, /proc
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SDB_UUID="159bacc4-38ae-4adb-bf25-7a5091c308ca"
SDD_UUID="5ce32377-6072-4c07-a066-807e89a5d48b"

# Khóa cứng tuyệt đối KHÔNG BAO GIỜ XÓA
HARD_BLOCKLIST=("labops" "lost+found" "LabOps-WS")

# Mount points hệ thống tuyệt đối KHÔNG BAO GIỜ ĐƯỢC NHẬN LÀ Ổ DỌN DẸP
FORBIDDEN_MOUNTS=("/" "/boot" "/boot/efi" "/home" "/var" "/etc" "/usr" "/mnt" "/mnt/HDD3" "/media" "/tmp")

# Nhóm B (Mặc định): dữ liệu cũ của tài khoản không còn trong /etc/passwd & LabOps
SDB_TIER1=(
  "anaconda" "anhlt" "annn" "cntt_1603" "cntt_1604" "cntt_1605"
  "dungkt" "face_recognition_sv" "hieult" "hoangnth" "khuongnd"
  "landing_page" "namtq" "similarity" "sonbh" "sonh"
  "tampm" "trangtt" "tuandv"
)

SDD_TIER1=(
  "aiot_project" "chiennc" "cntt_1601" "cntt_1602" "cntt_1606"
  "ducnt" "hoangnth" "huongnv" "quocvm" "sonh"
  "thangnt" "thanhnt" "trangnd" "uyenvnb" "vietlv"
)

# Nhóm A (Chỉ chọn khi truyền --include-review): thư mục trùng tên user/project mới hơn
SDB_REVIEW=(
  "face_recognition" "hieulc" "nckh_face" "nhannv" "quytm" "trangltt"
)

SDD_REVIEW=(
  "devbe" "nhannv" "tungtt"
)

MODE="dry-run"
INCLUDE_REVIEW=0
TARGET_DISK="both" # both | sdb | sdd
ASSUME_YES=0

usage() {
  cat <<'EOF'
Cách dùng: cleanup-sdb-sdd-safe.sh [TÙY CHỌN]

Mặc định script chạy ở chế độ --dry-run (chỉ kiểm tra và liệt kê, KHÔNG xóa file).

Tùy chọn:
  --dry-run          Mô phỏng kiểm tra an toàn và in danh sách (mặc định)
  --apply            Thực sự xóa các thư mục hợp lệ (yêu cầu chạy bằng sudo)
  --include-review   Xóa thêm Nhóm A (hieulc, trangltt, nhannv, quytm, face_recognition,
                     nckh_face trên sdb; devbe, nhannv, tungtt trên sdd)
  --only-sdb         Chỉ xử lý ổ SDB (UUID 159bacc4-38ae-4adb-bf25-7a5091c308ca)
  --only-sdd         Chỉ xử lý ổ SDD (UUID 5ce32377-6072-4c07-a066-807e89a5d48b)
  --yes              Bỏ qua câu hỏi nhập "XAC NHAN XOA" khi dùng kèm --apply
  -h, --help         Hiển thị hướng dẫn này

Ví dụ an toàn nhất:
  1) Kiểm tra trước (không xóa gì):
     bash scripts/ops/cleanup-sdb-sdd-safe.sh --dry-run
  2) Thực thi xóa Nhóm B (tài khoản cũ):
     sudo bash scripts/ops/cleanup-sdb-sdd-safe.sh --apply
  3) Nếu muốn xóa cả Nhóm A sau khi kiểm tra:
     sudo bash scripts/ops/cleanup-sdb-sdd-safe.sh --apply --include-review
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --apply)
      MODE="apply"
      shift
      ;;
    --include-review)
      INCLUDE_REVIEW=1
      shift
      ;;
    --only-sdb)
      TARGET_DISK="sdb"
      shift
      ;;
    --only-sdd)
      TARGET_DISK="sdd"
      shift
      ;;
    --yes)
      ASSUME_YES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "LỖI: Tham số không hợp lệ: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

for cmd in findmnt realpath python3 df awk grep; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "LỖI: Thiếu công cụ bắt buộc '$cmd'." >&2
    exit 1
  fi
done

if [[ "$MODE" == "apply" && "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "LỖI: Chế độ --apply bắt buộc chạy với quyền root/sudo (sudo bash $0 --apply ...)." >&2
  exit 1
fi

TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
LOG_FILE="/tmp/labops-cleanup-sdb-sdd-${TIMESTAMP}.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=============================================================================="
echo "🧹 LABOPS SAFE DISK CLEANUP (SDB & SDD)"
echo "Thời gian : $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "Chế độ    : $MODE (include_review=$INCLUDE_REVIEW, target=$TARGET_DISK)"
echo "Log file  : $LOG_FILE"
echo "=============================================================================="

# Thu thập danh sách đường dẫn cần bảo vệ từ hệ thống
DOCKER_MOUNTS=()
LABOPS_PATHS=()
PASSWD_HOMES=()
PROC_CWDS=()
PROC_CMDLINES=()

collect_protection_metadata() {
  # 1. Docker mounts
  if command -v docker >/dev/null 2>&1; then
    local cids
    cids="$(docker ps -aq 2>/dev/null || true)"
    if [[ -n "$cids" ]]; then
      while IFS= read -r msrc; do
        [[ -n "$msrc" ]] && DOCKER_MOUNTS+=("$(realpath -m "$msrc" 2>/dev/null || printf '%s' "$msrc")")
      done < <(echo "$cids" | xargs -r docker inspect --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' 2>/dev/null || true)
    fi
  fi

  # 2. LabOps Registry JSON (chỉ lấy thư mục workspace/thành viên, bỏ qua host_storage_root/mount_point của cả ổ)
  while IFS= read -r lpath; do
    [[ -n "$lpath" ]] && LABOPS_PATHS+=("$(realpath -m "$lpath" 2>/dev/null || printf '%s' "$lpath")")
  done < <(python3 - "$REPO_ROOT" <<'PYEOF'
import json, os, sys

repo_root = sys.argv[1]
paths = set()
candidates = [
    os.path.join(repo_root, "backend", "data", "members.json"),
    os.path.join(repo_root, "backend", "data", "v2_workspaces.json"),
]
target_keys = {
    "member_storage_directory",
    "workspace_dir",
    "storage_path",
    "member_storage_dir",
    "mount_path",
}

def walk(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in target_keys and isinstance(v, str) and (v.startswith("/media/") or v.startswith("/mnt/")):
                paths.add(v)
            else:
                walk(v)
    elif isinstance(obj, list):
        for item in obj:
            walk(item)

for fpath in candidates:
    if os.path.isfile(fpath):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                walk(json.load(f))
        except Exception:
            pass

for p in sorted(paths):
    print(p)
PYEOF
  )

  # 3. /etc/passwd home directories
  while IFS= read -r hdir; do
    [[ -n "$hdir" ]] && PASSWD_HOMES+=("$(realpath -m "$hdir" 2>/dev/null || printf '%s' "$hdir")")
  done < <(awk -F: '$6 ~ /^\// {print $6}' /etc/passwd 2>/dev/null || true)

  # 4. Snapshot tiến trình đang chạy (/proc/*/cwd và /proc/*/cmdline tham chiếu /media/)
  while IFS=$'\t' read -r kind pid val; do
    if [[ "$kind" == "CWD" ]]; then
      PROC_CWDS+=("${pid}|${val}")
    elif [[ "$kind" == "CMD" ]]; then
      PROC_CMDLINES+=("${pid}|${val}")
    fi
  done < <(python3 - "$$" <<'PYEOF'
import os, sys

self_pid = sys.argv[1]
for entry in os.listdir("/proc"):
    if not entry.isdigit() or entry == self_pid:
        continue
    pdir = os.path.join("/proc", entry)
    try:
        cwd = os.readlink(os.path.join(pdir, "cwd"))
        if cwd.startswith("/media/"):
            print(f"CWD\t{entry}\t{cwd}")
    except Exception:
        pass
    try:
        with open(os.path.join(pdir, "cmdline"), "rb") as f:
            cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "ignore").strip()
        if "/media/" in cmd:
            print(f"CMD\t{entry}\t{cmd}")
    except Exception:
        pass
PYEOF
  )
}

paths_overlap() {
  local a="$1"
  local b="$2"
  a="${a%/}"
  b="${b%/}"
  [[ -z "$a" || -z "$b" ]] && return 1
  if [[ "$a" == "$b" || "$a" == "$b/"* || "$b" == "$a/"* ]]; then
    return 0
  fi
  return 1
}

in_array() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    if [[ "$item" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

validate_mount_point() {
  local uuid="$1"
  local label="$2"
  local mnt
  mnt="$(findmnt -rn -o TARGET "UUID=$uuid" 2>/dev/null | head -n 1 || true)"
  if [[ -z "$mnt" ]]; then
    echo "⚠️  BỎ QUA $label (UUID=$uuid): hiện không được mount." >&2
    return 1
  fi
  local real_mnt
  real_mnt="$(realpath -m "$mnt")"
  if in_array "$real_mnt" "${FORBIDDEN_MOUNTS[@]}"; then
    echo "🛑 LỖI AN TOÀN: Mount point '$real_mnt' của $label nằm trong danh sách cấm hệ thống!" >&2
    return 1
  fi
  printf '%s' "$real_mnt"
  return 0
}

# Trả về 0 nếu an toàn để xóa, 1 nếu bỏ qua/bị chặn
preflight_check_target() {
  local mnt="$1"
  local name="$2"

  # Lớp 1: Kiểm tra tên hợp lệ, không phải blocklist, không phải symlink
  if [[ -z "$name" || "$name" == *"/"* || "$name" == *".."* || "$name" == "*"* ]]; then
    echo "   [BLOCK-L1] Tên thư mục không hợp lệ: '$name'"
    return 1
  fi
  if in_array "$name" "${HARD_BLOCKLIST[@]}"; then
    echo "   [PROTECTED] '$name' nằm trong Hard Blocklist -> GIỮ LẠI"
    return 1
  fi

  local raw_target="${mnt}/${name}"
  if [[ -L "$raw_target" ]]; then
    echo "   [BLOCK-L1] '$raw_target' là symlink -> BỎ QUA"
    return 1
  fi
  if [[ ! -d "$raw_target" ]]; then
    echo "   [MISSING]   '$raw_target' không tồn tại (có thể đã dọn trước đó)"
    return 1
  fi

  local real_target
  real_target="$(realpath -m "$raw_target")"
  if [[ "$real_target" != "${mnt}/${name}" || "$real_target" == "$mnt" ]]; then
    echo "   [BLOCK-L1] Đường dẫn thật '$real_target' không khớp cấu trúc con trực tiếp của '$mnt'"
    return 1
  fi

  # Lớp 2: Docker bind-mounts
  local dmnt
  for dmnt in "${DOCKER_MOUNTS[@]:-}"; do
    [[ -z "$dmnt" ]] && continue
    if paths_overlap "$real_target" "$dmnt"; then
      echo "   [BLOCK-L2] '$real_target' trùng/chứa Docker mount '$dmnt' -> GIỮ LẠI"
      return 1
    fi
  done

  # Lớp 3: LabOps members.json / v2_workspaces.json
  local lpath
  for lpath in "${LABOPS_PATHS[@]:-}"; do
    [[ -z "$lpath" ]] && continue
    if paths_overlap "$real_target" "$lpath"; then
      echo "   [BLOCK-L3] '$real_target' trùng/chứa đường dẫn LabOps '$lpath' -> GIỮ LẠI"
      return 1
    fi
  done

  # Lớp 4: /etc/passwd home directory
  local hdir
  for hdir in "${PASSWD_HOMES[@]:-}"; do
    [[ -z "$hdir" ]] && continue
    if paths_overlap "$real_target" "$hdir"; then
      echo "   [BLOCK-L4] '$real_target' trùng/chứa home user trong /etc/passwd ('$hdir') -> GIỮ LẠI"
      return 1
    fi
  done

  # Lớp 5: Tiến trình đang chạy (snapshot /proc/*/cwd và /proc/*/cmdline)
  local item pid cwd_link cmdline_str
  for item in "${PROC_CWDS[@]:-}"; do
    [[ -z "$item" ]] && continue
    pid="${item%%|*}"
    cwd_link="${item#*|}"
    if paths_overlap "$real_target" "$cwd_link"; then
      echo "   [BLOCK-L5] PID $pid đang có cwd tại '$cwd_link' -> GIỮ LẠI"
      return 1
    fi
  done
  for item in "${PROC_CMDLINES[@]:-}"; do
    [[ -z "$item" ]] && continue
    pid="${item%%|*}"
    cmdline_str="${item#*|}"
    if [[ "$cmdline_str" == *"$real_target"* ]]; then
      echo "   [BLOCK-L5] PID $pid đang tham chiếu '$real_target' trong cmdline -> GIỮ LẠI"
      return 1
    fi
  done

  return 0
}

collect_protection_metadata

echo ""
echo "--- DUNG LƯỢNG HIỆN TẠI TRƯỚC KHI XỬ LÝ ---"
df -hT | grep -E "Filesystem|/dev/sd" || df -hT

echo ""
echo "--- THÔNG TIN BẢO VỆ ĐANG ÁP DỤNG ---"
echo "• Hard Blocklist : ${HARD_BLOCKLIST[*]}"
echo "• Docker Mounts  : ${#DOCKER_MOUNTS[@]} đường dẫn đang theo dõi"
echo "• LabOps Paths   : ${#LABOPS_PATHS[@]} đường dẫn trong registry"
echo "• Passwd Homes   : ${#PASSWD_HOMES[@]} home directories"

CANDIDATE_TARGETS=()
CANDIDATE_LABELS=()

scan_disk() {
  local label="$1"
  local uuid="$2"
  shift 2
  local -a targets=("$@")

  local mnt
  if ! mnt="$(validate_mount_point "$uuid" "$label")"; then
    return 0
  fi

  echo ""
  echo "=== KIỂM TRA Ổ $label ($mnt) ==="
  local name
  for name in "${targets[@]}"; do
    if preflight_check_target "$mnt" "$name"; then
      local real_target="${mnt}/${name}"
      echo "   [SAFE-TO-DELETE] $real_target"
      CANDIDATE_TARGETS+=("$real_target")
      CANDIDATE_LABELS+=("$label")
    fi
  done

  echo "   --- Mục trên $label KHÔNG nằm trong danh sách xóa (sẽ được giữ nguyên) ---"
  local entry base
  for entry in "$mnt"/*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    base="$(basename "$entry")"
    if ! in_array "$entry" "${CANDIDATE_TARGETS[@]:-}"; then
      echo "   [KEEP] $entry"
    fi
  done
}

if [[ "$TARGET_DISK" == "both" || "$TARGET_DISK" == "sdb" ]]; then
  SDB_LIST=("${SDB_TIER1[@]}")
  if [[ "$INCLUDE_REVIEW" -eq 1 ]]; then
    SDB_LIST+=("${SDB_REVIEW[@]}")
  fi
  scan_disk "SDB" "$SDB_UUID" "${SDB_LIST[@]}"
fi

if [[ "$TARGET_DISK" == "both" || "$TARGET_DISK" == "sdd" ]]; then
  SDD_LIST=("${SDD_TIER1[@]}")
  if [[ "$INCLUDE_REVIEW" -eq 1 ]]; then
    SDD_LIST+=("${SDD_REVIEW[@]}")
  fi
  scan_disk "SDD" "$SDD_UUID" "${SDD_LIST[@]}"
fi

echo ""
echo "=============================================================================="
echo "Tổng số thư mục hợp lệ sẵn sàng xóa: ${#CANDIDATE_TARGETS[@]}"
echo "=============================================================================="

if [[ "${#CANDIDATE_TARGETS[@]}" -eq 0 ]]; then
  echo "Không có thư mục nào cần xóa."
  exit 0
fi

if [[ "$MODE" == "dry-run" ]]; then
  echo ""
  echo "✅ CHẾ ĐỘ DRY-RUN HOÀN TẤT — CHƯA XÓA BẤT KỲ DỮ LIỆU NÀO."
  echo "Nếu danh sách [SAFE-TO-DELETE] và [KEEP] ở trên đã đúng ý bạn, hãy chạy:"
  if [[ "$INCLUDE_REVIEW" -eq 1 ]]; then
    echo "  sudo bash $0 --apply --include-review"
  else
    echo "  sudo bash $0 --apply"
  fi
  exit 0
fi

if [[ "$ASSUME_YES" -ne 1 ]]; then
  echo ""
  echo "⚠️  CẢNH BÁO: Bạn sắp XÓA VĨNH VIỄN ${#CANDIDATE_TARGETS[@]} thư mục [SAFE-TO-DELETE] ở trên."
  echo "Các thư mục [KEEP], [PROTECTED], labops (ws-tranglt), LabOps-WS và lost+found sẽ được GIỮ NGUYÊN."
  read -r -p "Nhập chính xác 'XAC NHAN XOA' để tiếp tục: " CONFIRM
  if [[ "$CONFIRM" != "XAC NHAN XOA" ]]; then
    echo "Đã hủy thao tác xóa theo yêu cầu."
    exit 1
  fi
fi

echo ""
echo "--- BẮT ĐẦU XÓA AN TOÀN ---"
DELETED_COUNT=0
FAILED_COUNT=0
for idx in "${!CANDIDATE_TARGETS[@]}"; do
  target="${CANDIDATE_TARGETS[$idx]}"
  disk_lbl="${CANDIDATE_LABELS[$idx]}"
  # Kiểm tra lại lần cuối trước khi rm
  if [[ ! -d "$target" || -L "$target" ]]; then
    echo "⚠️  Bỏ qua '$target' (không còn là thư mục hợp lệ)"
    continue
  fi
  echo "🗑️  [$disk_lbl] Đang xóa: $target"
  if rm -rf --one-file-system -- "$target"; then
    DELETED_COUNT=$((DELETED_COUNT + 1))
  else
    echo "❌ Lỗi khi xóa: $target"
    FAILED_COUNT=$((FAILED_COUNT + 1))
  fi
done

echo ""
echo "--- DUNG LƯỢNG SAU KHI DỌN DẸP ---"
df -hT | grep -E "Filesystem|/dev/sd" || df -hT
echo ""
echo "=============================================================================="
echo "HOÀN TẤT: Đã xóa $DELETED_COUNT thư mục, lỗi $FAILED_COUNT thư mục."
echo "Nhật ký chi tiết đã lưu tại: $LOG_FILE"
echo "=============================================================================="
