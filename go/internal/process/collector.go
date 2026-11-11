package process

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"os/user"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/gpu"
)

var sensitiveArgsPattern = regexp.MustCompile(`(?i)(--(?:password|token|secret|key|api_key|auth|credential)[=\s]+)([^\s]+)`)

func sanitizeCmdline(cmd string) string {
	if cmd == "" {
		return ""
	}
	return sensitiveArgsPattern.ReplaceAllString(cmd, "${1}********")
}

func formatElapsedTime(seconds float64) string {
	tot := int64(math.Max(0, seconds))
	days := tot / 86400
	hours := (tot % 86400) / 3600
	mins := (tot % 3600) / 60
	secs := tot % 60
	if days > 0 {
		return fmt.Sprintf("%dd %02dh %02dm", days, hours, mins)
	}
	return fmt.Sprintf("%02dh %02dm %02ds", hours, mins, secs)
}

// Collector quản lý quét /proc định kỳ và lưu cache hiệu năng cao
type Collector struct {
	serverID     string
	gpuCol       *gpu.Collector
	mu           sync.RWMutex
	prevCPUTimes map[int]uint64
	lastTotalCPU uint64
	uidToUser    map[int]string
	latest       *ProcessReport
	lastScan     time.Time
}

// NewCollector khởi tạo Process Collector
func NewCollector(serverID string, gpuCol *gpu.Collector) *Collector {
	c := &Collector{
		serverID:     serverID,
		gpuCol:       gpuCol,
		prevCPUTimes: make(map[int]uint64),
		uidToUser:    make(map[int]string),
	}
	c.initUserCache()
	return c
}

func (c *Collector) initUserCache() {
	c.uidToUser[0] = "root"
	c.uidToUser[1000] = "aiot-1"
	c.uidToUser[1001] = "nhannv"
}

func (c *Collector) resolveUserLocked(uid int) string {
	if name, ok := c.uidToUser[uid]; ok {
		return name
	}
	u, err := user.LookupId(strconv.Itoa(uid))
	if err == nil {
		c.uidToUser[uid] = u.Username
		return u.Username
	}
	name := strconv.Itoa(uid)
	c.uidToUser[uid] = name
	return name
}

func (c *Collector) getUserName(uid int) string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.resolveUserLocked(uid)
}

// readTotalCPUJiffies đọc tổng jiffies CPU hiện tại từ /proc/stat
func readTotalCPUJiffies() uint64 {
	data, err := os.ReadFile("/proc/stat")
	if err != nil {
		return 0
	}
	lines := strings.Split(string(data), "\n")
	if len(lines) > 0 {
		fields := strings.Fields(lines[0])
		if len(fields) > 4 && fields[0] == "cpu" {
			var total uint64
			for _, f := range fields[1:] {
				v, _ := strconv.ParseUint(f, 10, 64)
				total += v
			}
			return total
		}
	}
	return 0
}

// readSystemUptimeSeconds đọc thời gian uptime hệ thống từ /proc/uptime
func readSystemUptimeSeconds() float64 {
	data, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return 0
	}
	fields := strings.Fields(string(data))
	if len(fields) > 0 {
		v, _ := strconv.ParseFloat(fields[0], 64)
		return v
	}
	return 0
}

// extractContainerInfo trích xuất container_id và workspace từ /proc/{pid}/cgroup
func extractContainerInfo(pid int) (string, string) {
	cgroupPath := filepath.Join("/proc", strconv.Itoa(pid), "cgroup")
	data, err := os.ReadFile(cgroupPath)
	if err != nil {
		return "", ""
	}

	lines := strings.Split(string(data), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		// Docker scope format: 0::/system.slice/docker-<64char_id>.scope
		if strings.Contains(line, "docker-") && strings.Contains(line, ".scope") {
			start := strings.Index(line, "docker-") + len("docker-")
			end := strings.Index(line, ".scope")
			if start < end {
				fullID := line[start:end]
				shortID := fullID
				if len(shortID) > 12 {
					shortID = shortID[:12]
				}
				return shortID, ""
			}
		} else if strings.Contains(line, "/docker/") {
			parts := strings.Split(line, "/docker/")
			if len(parts) > 1 {
				fullID := strings.TrimSpace(parts[1])
				shortID := fullID
				if len(shortID) > 12 {
					shortID = shortID[:12]
				}
				return shortID, ""
			}
		} else if strings.Contains(line, "lxc.payload.") {
			parts := strings.Split(line, "lxc.payload.")
			if len(parts) > 1 {
				sub := strings.Split(parts[1], "/")[0]
				return sub, sub
			}
		}
	}
	return "", ""
}

// IsDesktopOrBackgroundService nhận diện các dịch vụ desktop / user systemd / IDE tooling
func IsDesktopOrBackgroundService(name, cmdline string) bool {
	nameLower := strings.ToLower(name)
	cmdLower := strings.ToLower(cmdline)

	// 1. Dịch vụ desktop / systemd user session
	desktopServices := []string{
		"systemd", "sd-pam", "pulseaudio", "gnome-keyring-daemon",
		"dbus-daemon", "dbus-launch", "at-spi-bus-launcher", "at-spi2",
		"gvfsd", "dconf-service", "xdg-desktop-portal",
		"xdg-permission-store", "xdg-document-portal", "applet.py",
	}
	for _, ds := range desktopServices {
		if nameLower == ds || strings.HasPrefix(nameLower, ds) || strings.Contains(cmdLower, "/"+ds) {
			return true
		}
	}
	if strings.Contains(cmdLower, "system-config-printer") {
		return true
	}

	// 2. IDE tooling & helpers (VS Code, JetBrains, Copilot)
	ideHelpers := []string{
		"code-server",
		"server-main.js",
		"bootstrap-fork",
		"copilot-runtime",
		"command-shell",
		"agent host",
		"vscode-server",
		".vscode-server",
	}
	for _, ih := range ideHelpers {
		if strings.Contains(cmdLower, ih) {
			return true
		}
	}

	return false
}

// IsIdleShell nhận diện shell chờ lệnh (interactive shell không có lệnh script con)
func IsIdleShell(name, cmdline string) bool {
	nameLower := strings.ToLower(name)
	if nameLower != "bash" && nameLower != "sh" && nameLower != "zsh" && nameLower != "-bash" && nameLower != "-sh" {
		return false
	}
	fields := strings.Fields(cmdline)
	if len(fields) <= 1 {
		return true
	}
	cmdLower := strings.ToLower(cmdline)
	if strings.Contains(cmdLower, "shellintegration") {
		if !strings.Contains(cmdLower, " -c ") && !strings.Contains(cmdLower, ".py") {
			return true
		}
	}
	if strings.Contains(cmdLower, "--login") || strings.Contains(cmdLower, "-i") {
		if !strings.Contains(cmdLower, " -c ") && !strings.Contains(cmdLower, ".py") && !strings.Contains(cmdLower, ".sh") {
			return true
		}
	}
	if len(fields) == 2 && (fields[1] == "-i" || fields[1] == "-l" || fields[1] == "--login") {
		return true
	}
	return false
}

// IsWorkloadCandidate kiểm tra xem tiến trình có phải là workload người dùng chủ động chạy
func IsWorkloadCandidate(name, cmdline string, isGPU bool, vramMB uint64) (bool, string) {
	nameLower := strings.ToLower(name)
	cmdLower := strings.ToLower(cmdline)

	// 1. GPU processes là workload ưu tiên cao
	if isGPU || vramMB > 0 {
		return true, AttributionVerifiedWorkload
	}

	// 2. Machine Learning / AI / Data Scripts
	if strings.Contains(cmdLower, "python") || strings.Contains(cmdLower, "torchrun") ||
		strings.Contains(cmdLower, "accelerate") || strings.Contains(cmdLower, "deepspeed") ||
		strings.Contains(cmdLower, "vllm") || strings.Contains(cmdLower, "ollama") {
		// Trừ các python applet hệ thống và LabOps control plane backend
		if strings.Contains(cmdLower, "system-config-printer") || strings.Contains(cmdLower, "applet.py") ||
			strings.Contains(cmdLower, "backend/app/main") || strings.Contains(cmdLower, "app/main.py") ||
			strings.Contains(cmdLower, "uvicorn") || strings.Contains(cmdLower, "app.main") ||
			strings.Contains(cmdLower, "admin_labops_control_center/backend") ||
			strings.Contains(cmdLower, "backend/.venv") ||
			strings.Contains(cmdLower, "resource_tracker") {
			return false, AttributionSystemProcess
		}
		return true, AttributionVerifiedWorkload
	}

	// 3. Jupyter / Notebooks
	if strings.Contains(cmdLower, "jupyter") || strings.Contains(cmdLower, "ipykernel") {
		return true, AttributionVerifiedWorkload
	}

	// 4. Các ngôn ngữ lập trình / công cụ tính toán do user chạy
	computeKeywords := []string{
		"pytest", "cargo run", "go run", "julia", "matlab", "octave", "rscript",
	}
	for _, kw := range computeKeywords {
		if strings.Contains(cmdLower, kw) {
			return true, AttributionVerifiedWorkload
		}
	}

	// 5. Script bash/sh người dùng chủ động chạy file (ví dụ: ./run.sh, bash train.sh)
	if (nameLower == "bash" || nameLower == "sh" || nameLower == "zsh") && (strings.Contains(cmdLower, ".sh") || strings.Contains(cmdLower, ".py")) {
		if !strings.Contains(cmdLower, "shellintegration") && !strings.Contains(cmdLower, ".vscode-server") {
			return true, AttributionLikelyWorkload
		}
	}

	// 6. User binary chạy trực tiếp (như ./run, ./sim, nohup ./...)
	if strings.HasPrefix(cmdLower, "./") || strings.Contains(cmdLower, "nohup ") || strings.Contains(cmdLower, "tmux ") || strings.Contains(cmdLower, "screen ") {
		if !IsDesktopOrBackgroundService(name, cmdline) && !IsIdleShell(name, cmdline) {
			return true, AttributionLikelyWorkload
		}
	}

	return false, AttributionUnknown
}

// Collect quét danh sách tiến trình từ /proc
func (c *Collector) Collect(filterType string) *ProcessReport {
	c.mu.Lock()
	defer c.mu.Unlock()

	// Cache 1.5 giây cho filter all
	if time.Since(c.lastScan) < 1500*time.Millisecond && c.latest != nil && filterType == "all" {
		return c.latest
	}

	uptimeSec := readSystemUptimeSeconds()
	bootTime := time.Now().Add(-time.Duration(uptimeSec * float64(time.Second)))

	totalJiffies := readTotalCPUJiffies()
	totalJiffiesDiff := float64(totalJiffies - c.lastTotalCPU)
	c.lastTotalCPU = totalJiffies

	// Đọc tổng dung lượng RAM vật lý
	var totalRAMBytes uint64 = 125 * 1024 * 1024 * 1024 // 125GB default
	if memInfo, err := os.ReadFile("/proc/meminfo"); err == nil {
		for _, line := range strings.Split(string(memInfo), "\n") {
			if strings.HasPrefix(line, "MemTotal:") {
				fields := strings.Fields(line)
				if len(fields) >= 2 {
					if kb, err := strconv.ParseUint(fields[1], 10, 64); err == nil {
						totalRAMBytes = kb * 1024
					}
				}
				break
			}
		}
	}

	// Đọc bảng GPU compute apps từ GPU collector
	gpuProcessMap := make(map[int]struct {
		gpuIndex int
		gpuUUID  string
		vramMB   uint64
	})

	if c.gpuCol != nil {
		gpuReport := c.gpuCol.GetLatest()
		if gpuReport != nil {
			for _, g := range gpuReport.GPUs {
				for _, p := range g.ActiveProcesses {
					gpuProcessMap[p.PID] = struct {
						gpuIndex int
						gpuUUID  string
						vramMB   uint64
					}{
						gpuIndex: g.Index,
						gpuUUID:  g.UUID,
						vramMB:   p.UsedMemoryMB,
					}
				}
			}
		}
	}

	entries, err := os.ReadDir("/proc")
	if err != nil {
		return &ProcessReport{
			Source:      "labops-go-agent",
			CollectedAt: time.Now().UTC().Format(time.RFC3339),
			ServerID:    c.serverID,
			TotalCount:  0,
			Processes:   []ProcessInfo{},
		}
	}

	procs := make([]ProcessInfo, 0, 128)
	newCPUTimes := make(map[int]uint64)
	pageSize := uint64(syscall.Getpagesize())

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		pid, err := strconv.Atoi(entry.Name())
		if err != nil || pid <= 0 {
			continue
		}

		pidPath := filepath.Join("/proc", entry.Name())

		// Đọc /proc/[pid]/stat
		statData, err := os.ReadFile(filepath.Join(pidPath, "stat"))
		if err != nil {
			continue
		}

		statStr := string(statData)
		firstParen := strings.IndexByte(statStr, '(')
		lastParen := strings.LastIndexByte(statStr, ')')
		if firstParen == -1 || lastParen == -1 || lastParen <= firstParen {
			continue
		}

		procName := statStr[firstParen+1 : lastParen]
		afterParen := strings.Fields(statStr[lastParen+2:])
		if len(afterParen) < 22 {
			continue
		}

		state := afterParen[0]
		ppid, _ := strconv.Atoi(afterParen[1])
		pgid, _ := strconv.Atoi(afterParen[2])
		utime, _ := strconv.ParseUint(afterParen[11], 10, 64)
		stime, _ := strconv.ParseUint(afterParen[12], 10, 64)
		startTimeTicks, _ := strconv.ParseUint(afterParen[19], 10, 64)
		vsize, _ := strconv.ParseUint(afterParen[20], 10, 64)
		rssPages, _ := strconv.ParseUint(afterParen[21], 10, 64)

		rssBytes := rssPages * pageSize
		procTotalJiffies := utime + stime
		newCPUTimes[pid] = procTotalJiffies

		var cpuPct float64
		if prev, ok := c.prevCPUTimes[pid]; ok && totalJiffiesDiff > 0 && procTotalJiffies >= prev {
			cpuPct = math.Round(((float64(procTotalJiffies-prev)/totalJiffiesDiff)*100.0)*10) / 10
		}

		memPct := math.Round((float64(rssBytes)/float64(totalRAMBytes))*1000) / 10
		rssMB := math.Round((float64(rssBytes)/(1024*1024))*10) / 10
		vmsMB := math.Round((float64(vsize)/(1024*1024))*10) / 10

		// Tính toán start_time và elapsed_time chuẩn
		// CLK_TCK = 100 on Linux x86_64
		startSecAfterBoot := float64(startTimeTicks) / 100.0
		procStartTime := bootTime.Add(time.Duration(startSecAfterBoot * float64(time.Second)))
		elapsedSec := math.Max(0, uptimeSec-startSecAfterBoot)
		elapsedStr := formatElapsedTime(elapsedSec)
		startTimeStr := procStartTime.Format("2006-01-02 15:04:05")

		// Đọc UID từ /proc/[pid]/status
		uid := 0
		if statusData, err := os.ReadFile(filepath.Join(pidPath, "status")); err == nil {
			scanner := bufio.NewScanner(strings.NewReader(string(statusData)))
			for scanner.Scan() {
				line := scanner.Text()
				if strings.HasPrefix(line, "Uid:") {
					fields := strings.Fields(line)
					if len(fields) >= 2 {
						uid, _ = strconv.Atoi(fields[1])
					}
					break
				}
			}
		}

		userName := c.resolveUserLocked(uid)

		// Đọc cmdline đầy đủ và sanitize
		cmdline := procName
		if cmdData, err := os.ReadFile(filepath.Join(pidPath, "cmdline")); err == nil && len(cmdData) > 0 {
			cmdStr := strings.ReplaceAll(string(cmdData), "\x00", " ")
			cmdStr = strings.TrimSpace(cmdStr)
			if cmdStr != "" {
				cmdline = sanitizeCmdline(cmdStr)
			}
		}

		// Nhận diện Container / Workspace từ cgroup
		containerID, workspace := extractContainerInfo(pid)
		if containerID != "" && workspace == "" {
			workspace = fmt.Sprintf("docker-%s", containerID)
		}

		// Ánh xạ GPU
		gpuInfo, isGPU := gpuProcessMap[pid]
		var gpuIdx *int
		var gpuUUID string
		var vramMB uint64
		if isGPU {
			gpuIdx = &gpuInfo.gpuIndex
			gpuUUID = gpuInfo.gpuUUID
			vramMB = gpuInfo.vramMB
		}

		// Định danh an toàn ProcessRef
		ref := BuildRef(c.serverID, containerID, pid, startTimeTicks)

		// PHÂN LOẠI TIẾN TRÌNH THEO 4 NHÓM VÀ ATTRIBUTION NGUỒN GỐC THỰC THI
		var classification string
		var attribution string
		isProtected := false
		isUserWorkload := false

		// 1. Kiểm tra nếu là tiến trình hệ thống cốt lõi
		cmdLower := strings.ToLower(cmdline)
		isHostService := false
		for _, kw := range ProtectedCmdKeywords {
			if strings.Contains(cmdLower, kw) {
				isHostService = true
				break
			}
		}

		if pid <= 1 || ppid == 2 || ProtectedProcessNames[strings.ToLower(procName)] || isHostService ||
			strings.Contains(procName, "sshd") || uid == 0 || uid < 1000 {
			classification = ClassSystemProcess
			attribution = AttributionSystemProcess
			isProtected = true
			isUserWorkload = false
		} else {
			// Thuộc tài khoản user (UID >= 1000) hoặc trong Container
			// Kiểm tra xem có phải là background/desktop service hay IDE tooling hay idle shell không
			if IsDesktopOrBackgroundService(procName, cmdline) {
				classification = ClassMemberProcess
				attribution = AttributionBackgroundProcess
				isUserWorkload = false
			} else if IsIdleShell(procName, cmdline) {
				classification = ClassMemberProcess
				attribution = AttributionBackgroundProcess
				isUserWorkload = false
			} else {
				// Ứng viên cho User Workload
				isWorkload, attr := IsWorkloadCandidate(procName, cmdline, isGPU, vramMB)
				if isWorkload {
					isUserWorkload = true
					attribution = attr
					if isGPU {
						classification = ClassGPUProcess
					} else {
						classification = ClassResearchJob
					}
				} else {
					// Tiến trình thông thường của member nhưng chưa rõ mục đích
					classification = ClassMemberProcess
					attribution = AttributionLikelyWorkload
					// Nếu CPU > 1.0% hoặc đang chạy tính toán
					if cpuPct > 1.0 {
						isUserWorkload = true
					} else {
						isUserWorkload = false
					}
				}
			}
		}

		pInfo := ProcessInfo{
			Ref:               ref,
			PID:               pid,
			PPID:              ppid,
			PGID:              pgid,
			UID:               uid,
			User:              userName,
			Name:              procName,
			CommandLine:       cmdline,
			Classification:    classification,
			Attribution:       attribution,
			IsUserWorkload:    isUserWorkload,
			IsRootWorkload:    isUserWorkload, // Tạm thời là root, sẽ gộp theo cây ở bước sau
			ChildrenCount:     0,
			TotalCPUPercent:   cpuPct,
			TotalMemoryRSSMB:  rssMB,
			ContainerID:       containerID,
			Workspace:         workspace,
			CPUPercent:        cpuPct,
			MemoryRSSMB:       rssMB,
			MemoryVMSMB:       vmsMB,
			MemoryPercent:     memPct,
			State:             state,
			StartTime:         startTimeStr,
			StartTimeTicks:    startTimeTicks,
			ElapsedTime:       elapsedStr,
			IsGPU:             isGPU,
			GPUIndex:          gpuIdx,
			GPUUUID:           gpuUUID,
			VRAMMB:            vramMB,
			IsProtected:       isProtected,
		}

		procs = append(procs, pInfo)
	}

	// GOM NHÓM TIẾN TRÌNH (PARENT - WORKERS HIERARCHY)
	// Xây dựng map PID -> index
	pidMap := make(map[int]int, len(procs))
	for i := range procs {
		pidMap[procs[i].PID] = i
	}

	// Xác định quan hệ Root Workload vs Worker con
	for i := range procs {
		p := &procs[i]
		if p.IsUserWorkload {
			if parentIdx, ok := pidMap[p.PPID]; ok {
				parent := &procs[parentIdx]
				if parent.IsUserWorkload && parent.User == p.User {
					p.IsRootWorkload = false
					p.ParentWorkloadPID = parent.PID
				}
			}
		}
	}

	// Tính tổng CPU và RAM gộp cho Root Workload (tránh double-counting)
	for i := range procs {
		root := &procs[i]
		if root.IsUserWorkload && root.IsRootWorkload {
			totalCPU := root.CPUPercent
			totalRSS := root.MemoryRSSMB
			childrenCount := 0

			var aggregateWorkers func(parentPID int)
			aggregateWorkers = func(parentPID int) {
				for j := range procs {
					child := &procs[j]
					if child.ParentWorkloadPID == parentPID && child.PID != parentPID {
						childrenCount++
						totalCPU += child.CPUPercent
						totalRSS += child.MemoryRSSMB
						aggregateWorkers(child.PID)
					}
				}
			}
			aggregateWorkers(root.PID)

			root.ChildrenCount = childrenCount
			root.TotalCPUPercent = math.Round(totalCPU*10) / 10
			root.TotalMemoryRSSMB = math.Round(totalRSS*10) / 10
		}
	}

	c.prevCPUTimes = newCPUTimes
	c.lastScan = time.Now()

	// Áp dụng bộ lọc cho report trả về
	filtered := make([]ProcessInfo, 0, len(procs))
	for _, p := range procs {
		if filterType == "workloads" || filterType == "workload" || filterType == "research" {
			// Chỉ lấy User Workload gốc (root)
			if p.IsUserWorkload && p.IsRootWorkload {
				filtered = append(filtered, p)
			}
		} else if filterType == "gpu" {
			if p.IsGPU {
				filtered = append(filtered, p)
			}
		} else if filterType == "system" {
			if p.Classification == ClassSystemProcess {
				filtered = append(filtered, p)
			}
		} else if filterType == "member" {
			if p.Classification == ClassMemberProcess || p.Classification == ClassResearchJob || p.IsUserWorkload {
				filtered = append(filtered, p)
			}
		} else {
			// "all" hoặc mặc định: trả về tất cả
			filtered = append(filtered, p)
		}
	}

	report := &ProcessReport{
		Source:      "labops-go-agent",
		CollectedAt: time.Now().UTC().Format(time.RFC3339),
		ServerID:    c.serverID,
		TotalCount:  len(filtered),
		Processes:   filtered,
	}

	if filterType == "all" {
		c.latest = report
	}
	return report
}

// GetProcessByPID tìm thông tin 1 tiến trình theo PID
func (c *Collector) GetProcessByPID(pid int) (*ProcessInfo, error) {
	report := c.Collect("all")
	for _, p := range report.Processes {
		if p.PID == pid {
			return &p, nil
		}
	}
	return nil, fmt.Errorf("không tìm thấy tiến trình PID %d trong bảng tiến trình hiện tại", pid)
}

// GetChildrenPIDs tìm tất cả PID con đệ quy của 1 PID
func (c *Collector) GetChildrenPIDs(parentPID int) []int {
	report := c.Collect("all")
	children := make([]int, 0)
	var findChildren func(p int)
	findChildren = func(p int) {
		for _, proc := range report.Processes {
			if proc.PPID == p {
				children = append(children, proc.PID)
				findChildren(proc.PID)
			}
		}
	}
	findChildren(parentPID)
	return children
}

// GetProcessTree xây dựng cây tiến trình đầy đủ cho 1 ref
func (c *Collector) GetProcessTree(ref string) (*ProcessInfo, error) {
	parsedRef, err := ParseRef(ref)
	if err != nil {
		return nil, err
	}

	report := c.Collect("all")
	var root *ProcessInfo
	for _, p := range report.Processes {
		if p.PID == parsedRef.PID && p.StartTimeTicks == parsedRef.StartTimeTicks {
			copyP := p
			root = &copyP
			break
		}
	}

	if root == nil {
		return nil, fmt.Errorf("PROCESS_NOT_FOUND: Không tìm thấy tiến trình với ref '%s'", ref)
	}

	// Đệ quy ghép children
	var buildTree func(p *ProcessInfo)
	buildTree = func(p *ProcessInfo) {
		p.Children = make([]ProcessInfo, 0)
		for _, child := range report.Processes {
			if child.PPID == p.PID {
				childCopy := child
				buildTree(&childCopy)
				p.Children = append(p.Children, childCopy)
			}
		}
	}

	buildTree(root)
	return root, nil
}
