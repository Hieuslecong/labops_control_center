# LABOPS REPOSITORY GUARD — DEPLOYMENT & ROLLBACK GUIDE (PHASE 17 & 19)
**Target:** Debian 13 Host (Control Plane & Node Agent)  
**Safety Classification:** Zero-Downtime Rollout & Instant Rollback  

---

## 1. HƯỚNG DẪN TRIỂN KHAI THEO GIAI ĐOẠN (SAFE ROLLOUT)

### Bước 1: Build & Cập nhật Binaries
```bash
export PATH="/home/aiot-1/.local/go/bin:/home/aiot-1/go/bin:$PATH"
cd go
go build -o ../bin/labops-agent ./cmd/labops-agent
go build -o ../bin/labops ./cmd/labops
cd ..
```

### Bước 2: Khởi động lại Go Node Agent
```bash
# Agent sẽ tự động nạp module Repository Guard trên port 9100
pkill -f "./bin/labops-agent" || true
./bin/labops-agent >/dev/null 2>&1 &
```

### Bước 3: Khởi động Backend Control Plane (FastAPI)
```bash
./backend/.venv/bin/python backend/app/main.py
```

### Bước 4: Kiểm tra và triển khai thí điểm Network Egress (Pilot Container Only)
```bash
sudo ./scripts/pilot_workspace_egress.sh ws-pilot
```

---

## 2. QUY TRÌNH HỦY BỎ & KHÔI PHỤC (ROLLBACK PROCEDURE)

Nếu phát hiện bất kỳ sự cố nào trong quá trình vận hành hoặc kiểm thử:

### 1. Hủy bỏ ngay lập tức Network Egress trên Pilot:
```bash
sudo ./scripts/pilot_workspace_rollback.sh
```
*Lệnh này gỡ bỏ hoàn toàn mọi iptables rules trong chain `LABOPS-PILOT-EGRESS` và khôi phục lưu thông mạng bình thường.*

### 2. Khôi phục Binary Agent cũ (nếu cần):
```bash
git checkout HEAD~1 -- go/
cd go && go build -o ../bin/labops-agent ./cmd/labops-agent && cd ..
pkill -f "./bin/labops-agent" || true
./bin/labops-agent >/dev/null 2>&1 &
```

### 3. Dọn dẹp dữ liệu tạm thời của Repository Guard:
```bash
rm -rf /tmp/labops-repositoryguard/quarantine/*
```
*Kho lưu trữ `approved_store` có thể được giữ lại để tái sử dụng hoặc xóa nếu cần làm mới.*
