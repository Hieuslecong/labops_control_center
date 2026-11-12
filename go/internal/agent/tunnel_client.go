package agent

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"math/big"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/config"
	"github.com/Hieuslecong/labops_control_center/go/internal/docker"
	"github.com/Hieuslecong/labops_control_center/go/internal/gpu"
	"github.com/Hieuslecong/labops_control_center/go/internal/host"
	"github.com/Hieuslecong/labops_control_center/go/internal/pki"
	"github.com/Hieuslecong/labops_control_center/go/internal/process"
	pb "github.com/Hieuslecong/labops_control_center/go/proto/labops/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
)

// TunnelClient quản lý kết nối gRPC streaming mTLS từ Agent lên Gateway
type TunnelClient struct {
	cfg        *config.AgentConfig
	hostCol    *host.Collector
	gpuCol     *gpu.Collector
	procCol    *process.Collector
	procCtrl   *process.Controller
	dockerCtrl *docker.Controller
	logger     *slog.Logger
}

func NewTunnelClient(
	cfg *config.AgentConfig,
	hostCol *host.Collector,
	gpuCol *gpu.Collector,
	procCol *process.Collector,
	procCtrl *process.Controller,
	dockerCtrl *docker.Controller,
	logger *slog.Logger,
) *TunnelClient {
	if logger == nil {
		logger = slog.Default()
	}
	return &TunnelClient{
		cfg:        cfg,
		hostCol:    hostCol,
		gpuCol:     gpuCol,
		procCol:    procCol,
		procCtrl:   procCtrl,
		dockerCtrl: dockerCtrl,
		logger:     logger,
	}
}

// Start khởi chạy vòng lặp kết nối và duy trì tunnel tới Gateway
func (c *TunnelClient) Start(ctx context.Context) {
	if !c.cfg.GatewayEnabled && c.cfg.GatewayURL == "" {
		c.logger.Info("Chế độ Gateway Tunnel đang tắt, Agent chạy ở chế độ standalone")
		return
	}

	go c.runLoop(ctx)
}

func (c *TunnelClient) runLoop(ctx context.Context) {
	backoff := 1 * time.Second
	maxBackoff := 30 * time.Second

	for {
		select {
		case <-ctx.Done():
			c.logger.Info("Dừng TunnelClient theo tín hiệu context")
			return
		default:
		}

		err := c.connectAndStream(ctx)
		if err != nil && !strings.Contains(err.Error(), "context canceled") {
			c.logger.Warn("Mất kết nối hoặc lỗi gRPC Tunnel tới Gateway, thử lại...",
				"err", err,
				"retry_in", backoff,
				"gateway_url", c.cfg.GatewayURL,
			)
		}

		// Backoff có jitter ngẫu nhiên
		jitterVal, _ := rand.Int(rand.Reader, big.NewInt(1000))
		jitter := time.Duration(jitterVal.Int64()) * time.Millisecond
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff + jitter):
		}

		backoff *= 2
		if backoff > maxBackoff {
			backoff = maxBackoff
		}
	}
}

func (c *TunnelClient) connectAndStream(ctx context.Context) error {
	caPEM, err := os.ReadFile(c.cfg.GatewayCAPath)
	if err != nil {
		return fmt.Errorf("không thể đọc CA cert từ %s: %w", c.cfg.GatewayCAPath, err)
	}

	certPEM, err := os.ReadFile(c.cfg.GatewayCertPath)
	if err != nil {
		return fmt.Errorf("không thể đọc client cert từ %s: %w", c.cfg.GatewayCertPath, err)
	}

	keyPEM, err := os.ReadFile(c.cfg.GatewayKeyPath)
	if err != nil {
		return fmt.Errorf("không thể đọc client key từ %s: %w", c.cfg.GatewayKeyPath, err)
	}

	serverName := c.cfg.GatewayServerName
	if serverName == "" {
		serverName = "gateway.control-plane.labops.local"
	}

	tlsCfg, err := pki.BuildClientTLSConfig(caPEM, certPEM, keyPEM, serverName)
	if err != nil {
		return fmt.Errorf("lỗi tạo client TLS config: %w", err)
	}

	c.logger.Info("Đang thiết lập kênh gRPC mTLS tới Gateway...", "url", c.cfg.GatewayURL)

	conn, err := grpc.DialContext(ctx, c.cfg.GatewayURL,
		grpc.WithTransportCredentials(credentials.NewTLS(tlsCfg)),
		grpc.WithBlock(),
		grpc.WithTimeout(10*time.Second),
		grpc.WithDefaultCallOptions(
			grpc.MaxCallRecvMsgSize(32*1024*1024),
			grpc.MaxCallSendMsgSize(32*1024*1024),
		),
	)
	if err != nil {
		return fmt.Errorf("không thể kết nối gRPC tới Gateway: %w", err)
	}
	defer conn.Close()

	grpcClient := pb.NewNodeAgentGatewayServiceClient(conn)
	stream, err := grpcClient.Tunnel(ctx)
	if err != nil {
		return fmt.Errorf("không thể mở stream gRPC: %w", err)
	}

	// 1. Gửi bản tin chào NodeHello
	gpuReport := c.gpuCol.GetLatest()
	gpuCount := 0
	if gpuReport != nil {
		gpuCount = gpuReport.Count
	}

	helloMsg := &pb.AgentMessage{
		MessageId:     newUUID(),
		TimestampUnix: time.Now().Unix(),
		NodeId:        c.cfg.ServerID,
		Payload: &pb.AgentMessage_Hello{
			Hello: &pb.NodeHello{
				NodeId:        c.cfg.ServerID,
				Hostname:      c.cfg.Hostname,
				Role:          "COMPUTE_NODE",
				AgentVersion:  "2.0.0",
				OsInfo:        "Debian 13 Linux amd64",
				KernelVersion: "Linux 6.12",
				StartTimeUnix: time.Now().Unix(),
				ManagementIp:  c.cfg.BindAddr,
				GpuCount:      int32(gpuCount),
			},
		},
	}

	if err := stream.Send(helloMsg); err != nil {
		return fmt.Errorf("lỗi gửi NodeHello: %w", err)
	}

	// 2. Chờ nhận RegistrationAck
	respMsg, err := stream.Recv()
	if err != nil {
		return fmt.Errorf("lỗi nhận phản hồi RegistrationAck từ Gateway: %w", err)
	}

	regAck := respMsg.GetRegistrationAck()
	if regAck == nil || !regAck.Approved {
		return fmt.Errorf("Gateway từ chối đăng ký Node: %v", respMsg)
	}

	c.logger.Info("✅ Đăng ký thành công với Gateway!",
		"node_id", regAck.NodeId,
		"cluster", regAck.ClusterId,
		"allowed_actions", regAck.AllowedActions,
	)

	// 3. Goroutine định kỳ gửi Heartbeat và Telemetry
	streamCtx, streamCancel := context.WithCancel(ctx)
	defer streamCancel()

	go c.telemetrySenderRoutine(streamCtx, stream)

	// 4. Vòng lặp chính đọc các yêu cầu lệnh từ Gateway
	for {
		gwMsg, err := stream.Recv()
		if err != nil {
			if err == io.EOF {
				return nil
			}
			return fmt.Errorf("lỗi đọc stream từ Gateway: %w", err)
		}

		if gwMsg == nil {
			continue
		}

		if req := gwMsg.GetCommandRequest(); req != nil {
			go c.handleCommandRequest(stream, req)
		}
	}
}

func (c *TunnelClient) telemetrySenderRoutine(ctx context.Context, stream pb.NodeAgentGatewayService_TunnelClient) {
	hbTicker := time.NewTicker(c.cfg.HostInterval)
	defer hbTicker.Stop()

	procTicker := time.NewTicker(c.cfg.ProcessInterval)
	defer procTicker.Stop()

	var seq int64 = 0
	uptimeStart := time.Now()

	for {
		select {
		case <-ctx.Done():
			return

		case <-hbTicker.C:
			seq++
			uptimeSec := int64(time.Since(uptimeStart).Seconds())

			// 1. Gửi Heartbeat
			hbMsg := &pb.AgentMessage{
				MessageId:     newUUID(),
				TimestampUnix: time.Now().Unix(),
				NodeId:        c.cfg.ServerID,
				Payload: &pb.AgentMessage_Heartbeat{
					Heartbeat: &pb.NodeHeartbeat{
						NodeId:         c.cfg.ServerID,
						SequenceNumber: seq,
						UptimeSeconds:  uptimeSec,
						Health: &pb.NodeHealthStatus{
							State: pb.NodeHealthStatus_HEALTHY,
						},
					},
				},
			}
			_ = stream.Send(hbMsg)

			// 2. Gửi Host Telemetry
			hostReport := c.hostCol.GetLatest()
			if hostReport != nil {
				hostProto := toProtoHost(c.cfg.ServerID, hostReport)
				_ = stream.Send(&pb.AgentMessage{
					MessageId:     newUUID(),
					TimestampUnix: time.Now().Unix(),
					NodeId:        c.cfg.ServerID,
					Payload: &pb.AgentMessage_HostTelemetry{
						HostTelemetry: hostProto,
					},
				})
			}

			// 3. Gửi GPU Telemetry
			gpuReport := c.gpuCol.GetLatest()
			if gpuReport != nil {
				gpuProto := toProtoGPU(c.cfg.ServerID, gpuReport)
				_ = stream.Send(&pb.AgentMessage{
					MessageId:     newUUID(),
					TimestampUnix: time.Now().Unix(),
					NodeId:        c.cfg.ServerID,
					Payload: &pb.AgentMessage_GpuTelemetry{
						GpuTelemetry: gpuProto,
					},
				})
			}

		case <-procTicker.C:
			// 4. Gửi Process Telemetry
			procReport := c.procCol.Collect("all")
			if procReport != nil {
				procProto := toProtoProcess(c.cfg.ServerID, procReport)
				_ = stream.Send(&pb.AgentMessage{
					MessageId:     newUUID(),
					TimestampUnix: time.Now().Unix(),
					NodeId:        c.cfg.ServerID,
					Payload: &pb.AgentMessage_ProcessTelemetry{
						ProcessTelemetry: procProto,
					},
				})
			}

			// 5. Gửi Workspace Telemetry
			workspaces, err := c.dockerCtrl.ListWorkspaces(ctx)
			if err == nil {
				wsProto := toProtoWorkspace(c.cfg.ServerID, workspaces)
				_ = stream.Send(&pb.AgentMessage{
					MessageId:     newUUID(),
					TimestampUnix: time.Now().Unix(),
					NodeId:        c.cfg.ServerID,
					Payload: &pb.AgentMessage_WorkspaceTelemetry{
						WorkspaceTelemetry: wsProto,
					},
				})
			}
		}
	}
}

func (c *TunnelClient) handleCommandRequest(stream pb.NodeAgentGatewayService_TunnelClient, req *pb.CommandRequest) {
	c.logger.Info("Nhận yêu cầu lệnh từ Gateway",
		"command_id", req.CommandId,
		"action", req.Action,
		"actor", req.ActorId,
	)

	start := time.Now()
	resp := &pb.CommandResponse{
		CommandId:       req.CommandId,
		RequestId:       req.RequestId,
		NodeId:          c.cfg.ServerID,
		ExecutedAtUnix:  time.Now().Unix(),
	}

	switch req.Action {
	case "INSPECT_HOST":
		h := c.hostCol.GetLatest()
		b, _ := json.Marshal(h)
		resp.Status = pb.CommandResponse_SUCCESS
		resp.ResultPayload = b

	case "INSPECT_GPUS":
		g := c.gpuCol.GetLatest()
		b, _ := json.Marshal(g)
		resp.Status = pb.CommandResponse_SUCCESS
		resp.ResultPayload = b

	case "COLLECT_TELEMETRY":
		snapshot := map[string]any{
			"host":       c.hostCol.GetLatest(),
			"gpu":        c.gpuCol.GetLatest(),
			"processes":  c.procCol.Collect("all"),
			"workspaces": nil,
		}
		if ws, err := c.dockerCtrl.ListWorkspaces(context.Background()); err == nil {
			snapshot["workspaces"] = ws
		}
		b, _ := json.Marshal(snapshot)
		resp.Status = pb.CommandResponse_SUCCESS
		resp.ResultPayload = b

	case "TERMINATE_PROCESS":
		ref := req.Parameters["ref"]
		if ref == "" {
			pidStr := req.Parameters["pid"]
			if pid, err := strconv.Atoi(pidStr); err == nil && pid > 0 {
				if pInfo, pErr := c.procCol.GetProcessByPID(pid); pErr == nil && pInfo != nil {
					ref = pInfo.Ref
				}
			}
		}
		if ref == "" {
			resp.Status = pb.CommandResponse_REJECTED_INVALID_PARAMS
			resp.ErrorCode = "INVALID_PROCESS_REF"
			resp.ErrorMessage = "Thiếu tham số ref hoặc không tìm thấy process tương ứng với PID"
		} else {
			res, termErr := c.procCtrl.TerminateProcess(ref, 10, true)
			if termErr != nil {
				resp.Status = pb.CommandResponse_FAILED
				resp.ErrorCode = "TERMINATE_FAILED"
				resp.ErrorMessage = termErr.Error()
			} else {
				resp.Status = pb.CommandResponse_SUCCESS
				b, _ := json.Marshal(res)
				resp.ResultPayload = b
			}
		}

	case "STOP_DOCKER_WORKSPACE":
		cid := req.Parameters["container_id"]
		if cid == "" {
			resp.Status = pb.CommandResponse_REJECTED_INVALID_PARAMS
			resp.ErrorCode = "INVALID_CONTAINER_ID"
			resp.ErrorMessage = "Thiếu tham số container_id"
		} else {
			stopErr := c.dockerCtrl.StopContainer(cid, 10)
			if stopErr != nil {
				resp.Status = pb.CommandResponse_FAILED
				resp.ErrorCode = "STOP_CONTAINER_FAILED"
				resp.ErrorMessage = stopErr.Error()
			} else {
				resp.Status = pb.CommandResponse_SUCCESS
				b, _ := json.Marshal(map[string]any{"stopped": cid, "status": "exited"})
				resp.ResultPayload = b
			}
		}

	default:
		resp.Status = pb.CommandResponse_REJECTED_UNAUTHORIZED
		resp.ErrorCode = "UNSUPPORTED_ACTION"
		resp.ErrorMessage = fmt.Sprintf("Hành động '%s' không được hỗ trợ trên node", req.Action)
	}

	resp.ExecutionDurationMs = time.Since(start).Milliseconds()

	resMsg := &pb.AgentMessage{
		MessageId:     newUUID(),
		TimestampUnix: time.Now().Unix(),
		NodeId:        c.cfg.ServerID,
		Payload: &pb.AgentMessage_CommandResponse{
			CommandResponse: resp,
		},
	}

	_ = stream.Send(resMsg)
}

// -------------------------------------------------------------
// HELPER CONVERTERS
// -------------------------------------------------------------

func toProtoHost(nodeID string, h *host.HostTelemetry) *pb.HostTelemetry {
	if h == nil {
		return nil
	}
	return &pb.HostTelemetry{
		NodeId:           nodeID,
		Hostname:         h.Hostname,
		CpuUsagePercent:  h.CPUUsagePercent,
		CpuCores:         int32(h.CPUCores),
		CpuModel:         h.CPUModel,
		RamTotalBytes:    uint64(h.RAMTotalGB * 1024 * 1024 * 1024),
		RamUsedBytes:     uint64(h.RAMUsedGB * 1024 * 1024 * 1024),
		RamUsagePercent:  h.RAMUsagePercent,
		DiskTotalBytes:   uint64(h.DiskTotalTB * 1024 * 1024 * 1024 * 1024),
		DiskUsedBytes:    uint64(h.DiskUsedTB * 1024 * 1024 * 1024 * 1024),
		DiskUsagePercent: h.DiskUsagePercent,
		LoadAverage:      h.LoadAvg,
		UptimeSeconds:    int64(h.UptimeSeconds),
		NetRxBytesSec:    uint64(h.NetworkInMbps * 125000),
		NetTxBytesSec:    uint64(h.NetworkOutMbps * 125000),
	}
}

func toProtoGPU(nodeID string, g *gpu.SystemGPUReport) *pb.GPUTelemetry {
	if g == nil {
		return nil
	}
	gpus := make([]*pb.GPUInfo, 0, len(g.GPUs))
	for _, it := range g.GPUs {
		procs := make([]*pb.GPUProcessInfo, 0, len(it.ActiveProcesses))
		for _, p := range it.ActiveProcesses {
			procs = append(procs, &pb.GPUProcessInfo{
				Pid:          int32(p.PID),
				ProcessName:  p.ProcessName,
				UsedMemoryMb: p.UsedMemoryMB,
				Username:     p.Username,
			})
		}

		gpus = append(gpus, &pb.GPUInfo{
			Index:                   int32(it.Index),
			Name:                    it.Name,
			Uuid:                    it.UUID,
			MemoryTotalMb:           it.MemoryTotalMB,
			MemoryUsedMb:            it.MemoryUsedMB,
			MemoryFreeMb:            it.MemoryFreeMB,
			GpuUtilizationPercent:   float64(it.UtilizationGPUPct),
			MemoryUtilizationPercent: float64(it.UtilizationMemPct),
			TemperatureCelsius:      int32(it.TemperatureC),
			PowerUsageWatts:         float64(it.PowerWatts),
			PowerLimitWatts:         float64(it.PowerLimitWatts),
			Processes:               procs,
		})
	}

	return &pb.GPUTelemetry{
		NodeId:        nodeID,
		DriverVersion: g.DriverVersion,
		GpuCount:      int32(g.Count),
		Gpus:          gpus,
	}
}

func toProtoProcess(nodeID string, p *process.ProcessReport) *pb.ProcessTelemetry {
	if p == nil {
		return nil
	}
	list := make([]*pb.ProcessInfo, 0, len(p.Processes))
	for _, it := range p.Processes {
		list = append(list, &pb.ProcessInfo{
			Ref:            it.Ref,
			Pid:            int32(it.PID),
			Ppid:           int32(it.PPID),
			Pgid:           int32(it.PGID),
			Uid:            uint32(it.UID),
			User:           it.User,
			Name:           it.Name,
			CommandLine:    it.CommandLine,
			Classification: it.Classification,
			Attribution:    it.Attribution,
			IsUserWorkload: it.IsUserWorkload,
			IsRootWorkload: it.IsRootWorkload,
			ChildrenCount:  int32(it.ChildrenCount),
			CpuPercent:     it.CPUPercent,
			MemoryRssMb:    it.MemoryRSSMB,
			State:          it.State,
			StartTime:      it.StartTime,
			ElapsedTime:    it.ElapsedTime,
			IsGpu:          it.IsGPU,
			IsProtected:    it.IsProtected,
		})
	}
	return &pb.ProcessTelemetry{
		NodeId:     nodeID,
		TotalCount: int32(p.TotalCount),
		Processes:  list,
	}
}

func toProtoWorkspace(nodeID string, ws []docker.WorkspaceContainer) *pb.WorkspaceTelemetry {
	list := make([]*pb.WorkspaceInfo, 0, len(ws))
	for _, it := range ws {
		list = append(list, &pb.WorkspaceInfo{
			ContainerId:    it.ID,
			Name:           it.Name,
			Owner:          it.MemberUsername,
			Status:         it.Status,
			Image:          it.Image,
			SshPort:        int32(it.SSHPort),
			CpuCoresLimit:  int32(it.CPULimitCores),
			RamMbLimit:     it.RAMLimitBytes / (1024 * 1024),
			CreatedAt:      it.CreatedAt,
		})
	}
	return &pb.WorkspaceTelemetry{
		NodeId:     nodeID,
		Workspaces: list,
	}
}

func newUUID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}
