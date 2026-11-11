package config

import (
	"os"
	"strconv"
	"strings"
	"time"
)

// AgentConfig chứa các thiết lập vận hành của LabOps Go Agent.
type AgentConfig struct {
	ServerID                 string        `json:"server_id"`
	ServerName               string        `json:"server_name"`
	Hostname                 string        `json:"hostname"`
	BindAddr                 string        `json:"bind_addr"`
	Port                     int           `json:"port"`
	SecretToken              string        `json:"-"`
	DockerSocket             string        `json:"docker_socket"`
	HostInterval             time.Duration `json:"host_interval"`
	GPUInterval              time.Duration `json:"gpu_interval"`
	ProcessInterval          time.Duration `json:"process_interval"`
	IsContainerized          bool          `json:"is_containerized"`
	RepoGuardDataDir         string        `json:"repo_guard_data_dir"`
}

// LoadFromEnv nạp cấu hình từ biến môi trường và phát hiện môi trường host thực tế.
func LoadFromEnv() *AgentConfig {
	cfg := &AgentConfig{
		ServerID:         getEnv("LABOPS_SERVER_ID", "track"),
		ServerName:       getEnv("LABOPS_SERVER_NAME", "Server 01 — Server Rack (aiotlab-rack • Cục bộ)"),
		BindAddr:         getEnv("LABOPS_AGENT_BIND_ADDR", "127.0.0.1"),
		Port:             getEnvInt("LABOPS_AGENT_PORT", 9100),
		SecretToken:      getEnv("LABOPS_AGENT_SECRET", "labops-local-agent-verified-token-2026"),
		DockerSocket:     getEnv("DOCKER_HOST", "/var/run/docker.sock"),
		HostInterval:     5 * time.Second,
		GPUInterval:      5 * time.Second,
		ProcessInterval:  10 * time.Second,
		RepoGuardDataDir: getEnv("LABOPS_REPO_GUARD_DATA_DIR", "/tmp/labops-repositoryguard"),
	}

	// Phát hiện hostname thực tế
	if h, err := os.ReadFile("/etc/hostname"); err == nil && len(strings.TrimSpace(string(h))) > 0 {
		cfg.Hostname = strings.TrimSpace(string(h))
	} else if hn, err := os.Hostname(); err == nil {
		cfg.Hostname = hn
	} else {
		cfg.Hostname = "aiotlab-rack"
	}

	// Kiểm tra containerized
	if _, err := os.Stat("/.dockerenv"); err == nil {
		cfg.IsContainerized = true
	}

	return cfg
}

func getEnv(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

func getEnvInt(key string, defaultVal int) int {
	if val := os.Getenv(key); val != "" {
		if i, err := strconv.Atoi(val); err == nil {
			return i
		}
	}
	return defaultVal
}

