package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/agent"
	"github.com/Hieuslecong/labops_control_center/go/internal/config"
	"github.com/Hieuslecong/labops_control_center/go/internal/docker"
	"github.com/Hieuslecong/labops_control_center/go/internal/gpu"
	"github.com/Hieuslecong/labops_control_center/go/internal/host"
	"github.com/Hieuslecong/labops_control_center/go/internal/process"
	"github.com/Hieuslecong/labops_control_center/go/internal/transport"
)

func main() {
	// 1. Cấu hình logger có cấu trúc slog
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
	slog.SetDefault(logger)

	logger.Info("==========================================================")
	logger.Info("🚀 KHỞI ĐỘNG LABOPS GO NODE AGENT (HIGH-PERFORMANCE)")
	logger.Info("==========================================================")

	// 2. Nạp cấu hình runtime
	cfg := config.LoadFromEnv()
	logger.Info("Cấu hình Node Agent",
		"server_id", cfg.ServerID,
		"server_name", cfg.ServerName,
		"hostname", cfg.Hostname,
		"bind_addr", cfg.BindAddr,
		"port", cfg.Port,
		"docker_socket", cfg.DockerSocket,
	)

	// 3. Khởi tạo các bộ thu thập dữ liệu (Zero fork-exec host collector)
	hostCollector := host.NewCollector(cfg)
	gpuCollector := gpu.NewCollector()
	procCollector := process.NewCollector(cfg.ServerID, gpuCollector)
	dockerCtrl := docker.NewController(cfg.DockerSocket)

	// 4. Khởi chạy goroutine lấy mẫu định kỳ trong nền (Background sampling)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	go func() {
		ticker := time.NewTicker(cfg.HostInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				hostCollector.Collect()
			}
		}
	}()

	go func() {
		gpuTicker := time.NewTicker(cfg.GPUInterval)
		defer gpuTicker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-gpuTicker.C:
				gpuCollector.Collect()
			}
		}
	}()

	// 5. Khởi chạy HTTP Server cho Node Agent
	server := transport.NewServer(cfg, hostCollector, gpuCollector, procCollector, dockerCtrl, logger)

	go func() {
		if err := server.Start(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("Lỗi dừng đột ngột HTTP Server của Agent", "err", err)
			os.Exit(1)
		}
	}()

	// 6. Khởi chạy gRPC mTLS Tunnel Client tới Gateway (nếu được cấu hình)
	procCtrl := process.NewController(procCollector)
	tunnelClient := agent.NewTunnelClient(cfg, hostCollector, gpuCollector, procCollector, procCtrl, dockerCtrl, logger)
	tunnelClient.Start(ctx)

	// 7. Xử lý tín hiệu tắt an toàn (Graceful Shutdown)
	stopChan := make(chan os.Signal, 1)
	signal.Notify(stopChan, os.Interrupt, syscall.SIGTERM)

	sig := <-stopChan
	logger.Info("Nhận tín hiệu dừng hệ thống", "signal", sig.String())

	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer shutdownCancel()

	if err := server.Shutdown(shutdownCtx); err != nil {
		logger.Error("Lỗi khi shutdown HTTP server", "err", err)
	}

	logger.Info("🛑 LabOps Go Node Agent đã dừng an toàn.")
}

