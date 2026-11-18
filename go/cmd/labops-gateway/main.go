package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/gateway"
	"github.com/Hieuslecong/labops_control_center/go/internal/pki"
)

func generateEphemeralHex(byteLen int) (string, error) {
	b := make([]byte, byteLen)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

func resolveDedicatedSecret(logger *slog.Logger, devEphemeral bool, envKeys ...string) string {
	for _, k := range envKeys {
		if val := strings.TrimSpace(os.Getenv(k)); val != "" {
			if len(val) < 16 {
				logger.Error("Secret cấu hình quá ngắn (tối thiểu 16 ký tự)", "env_var", k)
				os.Exit(1)
			}
			return val
		}
	}
	if devEphemeral {
		gen, err := generateEphemeralHex(32)
		if err != nil {
			logger.Error("Không thể sinh ephemeral dev secret", "err", err)
			os.Exit(1)
		}
		logger.Warn("⚠️ LABOPS_DEV_EPHEMERAL_SECRETS=true: đang dùng secret tạm thời cho môi trường phát triển", "env_var", envKeys[0])
		return gen
	}
	logger.Error("Thiếu biến môi trường secret bắt buộc (fail-closed, không dùng fallback mặc định)", "required_env", strings.Join(envKeys, " | "))
	os.Exit(1)
	return ""
}

func main() {
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
	slog.SetDefault(logger)

	logger.Info("==========================================================")
	logger.Info("🛡️ KHỞI ĐỘNG LABOPS ZERO-TRUST AGENT GATEWAY (PORT 9443)")
	logger.Info("==========================================================")

	grpcPort := getEnvInt("LABOPS_GATEWAY_GRPC_PORT", 9443)
	httpPort := getEnvInt("LABOPS_GATEWAY_HTTP_PORT", 9444)
	bindAddr := getEnvStr("LABOPS_GATEWAY_BIND_ADDR", "0.0.0.0")
	httpBind := getEnvStr("LABOPS_GATEWAY_HTTP_BIND", "127.0.0.1")
	pkiDir := getEnvStr("LABOPS_GATEWAY_PKI_DIR", "")
	devEphemeral := strings.EqualFold(strings.TrimSpace(os.Getenv("LABOPS_DEV_EPHEMERAL_SECRETS")), "true")

	// Tách biệt rõ secret từng mục đích (không fallback sang LABOPS_SESSION_SECRET / LABOPS_ADMIN_API_TOKEN)
	enrollmentSecret := resolveDedicatedSecret(logger, devEphemeral, "LABOPS_ENROLLMENT_SECRET", "LABOPS_GATEWAY_TOKEN_SECRET")
	controlSecret := resolveDedicatedSecret(logger, devEphemeral, "LABOPS_GATEWAY_CONTROL_SECRET")
	delegationSecret := resolveDedicatedSecret(logger, devEphemeral, "LABOPS_DELEGATION_SECRET")

	if pkiDir == "" {
		home, _ := os.UserHomeDir()
		defaultDir := filepath.Join(home, ".labops", "pki")
		if err := os.MkdirAll(defaultDir, 0700); err == nil {
			pkiDir = defaultDir
		} else {
			// Fallback sang data/pki trong thư mục làm việc hiện tại
			cwd, _ := os.Getwd()
			pkiDir = filepath.Join(cwd, "data", "pki")
			_ = os.MkdirAll(pkiDir, 0700)
		}
	}

	logger.Info("Cấu hình Agent Gateway",
		"grpc_port", grpcPort,
		"http_port", httpPort,
		"bind_addr", bindAddr,
		"pki_dir", pkiDir,
	)

	// 1. Khởi tạo / nạp Root CA
	ca, err := pki.GenerateOrLoadCA(pkiDir)
	if err != nil {
		logger.Error("Lỗi khởi tạo hoặc nạp LabOps Root CA", "err", err)
		os.Exit(1)
	}
	logger.Info("Root CA đã sẵn sàng", "cn", ca.Cert.Subject.CommonName, "serial", ca.Cert.SerialNumber.String())

	// 2. Phát hành chứng chỉ Server cho Gateway
	serverHosts := []string{
		"127.0.0.1",
		"localhost",
		"aiotlab-rack",
		"192.168.167.250",
		"gateway.control-plane.labops.local",
	}
	gwCert, err := pki.IssueGatewayCert(ca, serverHosts)
	if err != nil {
		logger.Error("Lỗi phát hành chứng chỉ Server mTLS cho Gateway", "err", err)
		os.Exit(1)
	}

	// 3. Khởi tạo Revocation & Token Manager với lưu trữ bền vững trên đĩa (deny-by-default)
	revocationFile := filepath.Join(pkiDir, "revocations.json")
	rm := pki.NewRevocationManagerWithPersistence(revocationFile)
	// Chỉ cấp allowlist ban đầu khi được chỉ định rõ trong LABOPS_ALLOWED_NODES
	if allowedEnv := strings.TrimSpace(os.Getenv("LABOPS_ALLOWED_NODES")); allowedEnv != "" {
		for _, rawNode := range strings.Split(allowedEnv, ",") {
			nid := strings.TrimSpace(rawNode)
			if nid != "" && !rm.IsNodeAllowed(nid) {
				rm.AllowNode(nid)
			}
		}
	}

	tm := pki.NewEnrollmentTokenManager(enrollmentSecret)

	// 4. Cấu hình TLS 1.3 mTLS cho gRPC
	serverTLS, err := pki.BuildServerTLSConfig(ca.CertPEM, gwCert.CertPEM, gwCert.KeyPEM, rm)
	if err != nil {
		logger.Error("Lỗi cấu hình Server TLS", "err", err)
		os.Exit(1)
	}

	// 5. Khởi tạo Gateway Server
	gwCfg := &gateway.Config{
		BindAddr:          bindAddr,
		GRPCPort:          grpcPort,
		RevocationManager: rm,
		DelegationSecret:  delegationSecret,
		RequireDelegation: true,
		Logger:            logger,
	}

	gwServer, err := gateway.NewServer(gwCfg, ca, serverTLS)
	if err != nil {
		logger.Error("Lỗi khởi tạo Gateway Server", "err", err)
		os.Exit(1)
	}

	// Khởi chạy gRPC Server trong goroutine
	go func() {
		if err := gwServer.Start(); err != nil {
			logger.Error("gRPC Server gặp sự cố dừng", "err", err)
		}
	}()

	// 6. Khởi tạo và chạy HTTP Control Plane API trên loopback với xác thực bắt buộc
	httpControlSrv := gateway.NewHTTPControlPlaneServer(
		httpBind,
		httpPort,
		gwServer.SessionManager(),
		ca,
		tm,
		rm,
		controlSecret,
		logger,
	)

	go func() {
		if err := httpControlSrv.Start(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("HTTP Control Plane API gặp sự cố dừng", "err", err)
		}
	}()

	// 7. Chờ tín hiệu dừng Graceful Shutdown
	stopChan := make(chan os.Signal, 1)
	signal.Notify(stopChan, os.Interrupt, syscall.SIGTERM)

	sig := <-stopChan
	logger.Info("Nhận tín hiệu dừng Gateway", "signal", sig.String())

	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer shutdownCancel()

	_ = httpControlSrv.Stop(shutdownCtx)
	gwServer.Stop()

	logger.Info("🛑 LabOps Agent Gateway đã dừng an toàn.")
}

func getEnvStr(key, defVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defVal
}

func getEnvInt(key string, defVal int) int {
	if val := os.Getenv(key); val != "" {
		if i, err := strconv.Atoi(val); err == nil {
			return i
		}
	}
	return defVal
}

