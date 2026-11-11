package process

import (
	"bufio"
	"math"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

// ProcessInfo lưu thông tin an toàn về một tiến trình đang chạy
type ProcessInfo struct {
	PID           int     `json:"pid"`
	PPID          int     `json:"ppid"`
	UID           int     `json:"uid"`
	User          string  `json:"user"`
	Name          string  `json:"name"`
	CommandLine   string  `json:"command_line,omitempty"` // Rút gọn an toàn
	CPUPercent    float64 `json:"cpu_percent"`
	MemoryRSSMB   float64 `json:"memory_rss_mb"`
	MemoryVMSMB   float64 `json:"memory_vms_mb"`
	MemoryPercent float64 `json:"memory_percent"`
	State         string  `json:"state"`
	StartTime     string  `json:"start_time"`
	ContainerID   string  `json:"container_id,omitempty"`
	IsGPU         bool    `json:"is_gpu"`
	IsSSH         bool    `json:"is_ssh"`
	IsResearch    bool    `json:"is_research"`
}

// ProcessReport báo cáo tổng quan tiến trình
type ProcessReport struct {
	Source      string        `json:"source"`
	CollectedAt string        `json:"collected_at"`
	ServerID    string        `json:"server_id"`
	TotalCount  int           `json:"total_count"`
	Processes   []ProcessInfo `json:"processes"`
}

// Collector quản lý quét /proc định kỳ
type Collector struct {
	serverID     string
	mu           sync.RWMutex
	prevCPUTimes map[int]uint64
	lastTotalCPU uint64
	uidToUser    map[int]string
	latest       *ProcessReport
	lastScan     time.Time
}

// NewCollector khởi tạo Process Collector
func NewCollector(serverID string) *Collector {
	c := &Collector{
		serverID:     serverID,
		prevCPUTimes: make(map[int]uint64),
		uidToUser:    make(map[int]string),
	}
	c.initUserCache()
	return c
}

func (c *Collector) initUserCache() {
	// Nạp trước các người dùng thông dụng
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

// Collect quét danh sách tiến trình từ /proc
func (c *Collector) Collect(filterType string) *ProcessReport {
	c.mu.Lock()
	defer c.mu.Unlock()

	if time.Since(c.lastScan) < 2*time.Second && c.latest != nil && filterType == "all" {
		return c.latest
	}

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
		if err != nil {
			continue
		}

		pidPath := filepath.Join("/proc", entry.Name())

		// Đọc /proc/[pid]/stat
		statData, err := os.ReadFile(filepath.Join(pidPath, "stat"))
		if err != nil {
			continue
		}

		statStr := string(statData)
		// Tìm vị trí ngoặc bao tên tiến trình (comm)
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
		utime, _ := strconv.ParseUint(afterParen[11], 10, 64)
		stime, _ := strconv.ParseUint(afterParen[12], 10, 64)
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

		// Đọc cmdline rút gọn an toàn (tối đa 120 ký tự, không lộ secrets)
		cmdline := procName
		if cmdData, err := os.ReadFile(filepath.Join(pidPath, "cmdline")); err == nil && len(cmdData) > 0 {
			cmdStr := strings.ReplaceAll(string(cmdData), "\x00", " ")
			cmdStr = strings.TrimSpace(cmdStr)
			if len(cmdStr) > 120 {
				cmdStr = cmdStr[:117] + "..."
			}
			if cmdStr != "" {
				cmdline = cmdStr
			}
		}

		isSSH := strings.Contains(procName, "sshd") || strings.Contains(cmdline, "sshd:")
		isResearch := strings.HasPrefix(userName, "test") || strings.HasPrefix(userName, "res_") ||
			strings.Contains(cmdline, "python") || strings.Contains(cmdline, "torch") ||
			strings.Contains(cmdline, "train") || strings.Contains(cmdline, "jupyter")

		pInfo := ProcessInfo{
			PID:           pid,
			PPID:          ppid,
			UID:           uid,
			User:          userName,
			Name:          procName,
			CommandLine:   cmdline,
			CPUPercent:    cpuPct,
			MemoryRSSMB:   rssMB,
			MemoryVMSMB:   vmsMB,
			MemoryPercent: memPct,
			State:         state,
			StartTime:     time.Now().Format("15:04:05"),
			IsSSH:         isSSH,
			IsResearch:    isResearch,
		}

		// Áp dụng bộ lọc
		if filterType == "research" && !isResearch {
			continue
		} else if filterType == "gpu" && !pInfo.IsGPU {
			continue
		}

		procs = append(procs, pInfo)
	}

	c.prevCPUTimes = newCPUTimes
	c.lastScan = time.Now()

	report := &ProcessReport{
		Source:      "labops-go-agent",
		CollectedAt: time.Now().UTC().Format(time.RFC3339),
		ServerID:    c.serverID,
		TotalCount:  len(procs),
		Processes:   procs,
	}

	if filterType == "all" {
		c.latest = report
	}
	return report
}
