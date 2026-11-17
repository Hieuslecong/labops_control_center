package main

import (
	"context"
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
	tokenSecret := getEnvStr("LABOPS_GATEWAY_TOKEN_SECRET", "")
	controlSecret := getEnvStr("LABOPS_GATEWAY_CONTROL_SECRET", "")
	if !gateway.IsLoopbackControlBind(httpBind) {
		logger.Error("LABOPS_GATEWAY_HTTP_BIND must be a loopback IP; public control-plane binding is forbidden")
		os.Exit(1)
	}
	if len(tokenSecret) < 32 {
		logger.Error("LABOPS_GATEWAY_TOKEN_SECRET must be explicitly configured with at least 32 characters")
		os.Exit(1)
	}
	if len(controlSecret) < 32 {
		logger.Error("LABOPS_GATEWAY_CONTROL_SECRET must be explicitly configured with at least 32 characters")
		os.Exit(1)
	}

	if pkiDir == "" {
		home, err := os.UserHomeDir()
		if err != nil || home == "" {
			logger.Error("Cannot resolve the service home directory for PKI state")
			os.Exit(1)
		}
		defaultDir := filepath.Join(home, ".labops", "pki")
		if err := os.MkdirAll(defaultDir, 0700); err != nil {
			logger.Error("Cannot create PKI state directory", "err", err)
			os.Exit(1)
		}
		pkiDir = defaultDir
	}

	logger.Info("Cấu hình Agent Gateway",
		"grpc_port", grpcPort,
		"http_port", httpPort,
		"bind_addr", bindAddr,
		"pki_dir", pkiDir,
	)

	// Load and validate authorization state before generating or serving any PKI material.
	revocationFile := filepath.Join(pkiDir, "revocations.json")
	rm := pki.NewRevocationManagerWithPersistence(revocationFile)
	if err := rm.LoadError(); err != nil {
		logger.Error("Invalid revocation state", "err", err)
		os.Exit(1)
	}
	if !rm.StatePresent() {
		rawBootstrap := strings.TrimSpace(os.Getenv("LABOPS_GATEWAY_BOOTSTRAP_NODES"))
		if rawBootstrap == "" {
			logger.Error("Revocation state is missing; set LABOPS_GATEWAY_BOOTSTRAP_NODES explicitly before first startup")
			os.Exit(1)
		}
		if err := rm.BootstrapAllowedNodes(strings.Split(rawBootstrap, ",")); err != nil {
			logger.Error("Gateway allowlist bootstrap failed", "err", err)
			os.Exit(1)
		}
	}

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

	// 3. Token manager uses an explicitly configured secret; no default secret exists.
	tm := pki.NewEnrollmentTokenManager(tokenSecret)

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
