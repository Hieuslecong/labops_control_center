package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"maps"
	"strconv"
	"strings"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/config"
	"github.com/Hieuslecong/labops_control_center/go/internal/docker"
	"github.com/Hieuslecong/labops_control_center/go/internal/gateway"
	"github.com/Hieuslecong/labops_control_center/go/internal/gpu"
	"github.com/Hieuslecong/labops_control_center/go/internal/host"
	"github.com/Hieuslecong/labops_control_center/go/internal/process"
	"github.com/Hieuslecong/labops_control_center/go/internal/resources"
	"github.com/Hieuslecong/labops_control_center/go/internal/storage"
	pb "github.com/Hieuslecong/labops_control_center/go/proto/labops/v1"
)

// ExecuteCommandRequest chuẩn hóa request cho cả gRPC Tunnel và HTTP /api/v1/agent/execute.
type ExecuteCommandRequest struct {
	CommandID            string            `json:"command_id"`
	RequestID            string            `json:"request_id"`
	NodeID               string            `json:"node_id"`
	ActorID              string            `json:"actor_id"`
	Action               string            `json:"action"`
	Parameters           map[string]string `json:"parameters"`
	AuthorizationContext string            `json:"authorization_context"`
	IdempotencyKey       string            `json:"idempotency_key"`
	IssuedAtUnix         int64             `json:"issued_at_unix"`
	DeadlineSeconds      int64             `json:"deadline_seconds"`
}

// ExecuteCommandResponse chuẩn hóa kết quả trả về cho Control Plane.
type ExecuteCommandResponse struct {
	CommandID        string                           `json:"command_id"`
	RequestID        string                           `json:"request_id"`
	NodeID           string                           `json:"node_id"`
	Action           string                           `json:"action"`
	Success          bool                             `json:"success"`
	Status           string                           `json:"status"`
	ProtoStatus      pb.CommandResponse_CommandStatus `json:"-"`
	IdempotentReplay bool                             `json:"idempotent_replay,omitempty"`
	ErrorCode        string                           `json:"error_code,omitempty"`
	ErrorMessage     string                           `json:"error_message,omitempty"`
	Result           any                              `json:"result,omitempty"`
	ResultPayload    json.RawMessage                  `json:"-"`
	ExecutedAtUnix   int64                            `json:"executed_at_unix"`
	DurationMs       int64                            `json:"execution_duration_ms"`
}

// CommandExecutor dùng chung giữa gRPC Tunnel và HTTP /api/v1/agent/execute.
type CommandExecutor struct {
	cfg         *config.AgentConfig
	hostCol     *host.Collector
	gpuCol      *gpu.Collector
	procCol     *process.Collector
	procCtrl    *process.Controller
	dockerCtrl  *docker.Controller
	replayGuard *gateway.ReplayGuard
	logger      *slog.Logger
}

func NewCommandExecutor(
	cfg *config.AgentConfig,
	hostCol *host.Collector,
	gpuCol *gpu.Collector,
	procCol *process.Collector,
	procCtrl *process.Controller,
	dockerCtrl *docker.Controller,
	replayGuard *gateway.ReplayGuard,
	logger *slog.Logger,
) *CommandExecutor {
	if logger == nil {
		logger = slog.Default()
	}
	if replayGuard == nil {
		replayGuard = gateway.NewReplayGuard()
	}
	return &CommandExecutor{
		cfg:         cfg,
		hostCol:     hostCol,
		gpuCol:      gpuCol,
		procCol:     procCol,
		procCtrl:    procCtrl,
		dockerCtrl:  dockerCtrl,
		replayGuard: replayGuard,
		logger:      logger,
	}
}

func (e *CommandExecutor) Execute(ctx context.Context, req *ExecuteCommandRequest) *ExecuteCommandResponse {
	start := time.Now()
	if req == nil {
		return &ExecuteCommandResponse{
			Status: "REJECTED_INVALID_PARAMS", ProtoStatus: pb.CommandResponse_REJECTED_INVALID_PARAMS,
			ErrorCode: "EMPTY_REQUEST", ErrorMessage: "Request không được rỗng", ExecutedAtUnix: time.Now().Unix(),
		}
	}
	reqID := strings.TrimSpace(req.RequestID)
	if reqID == "" {
		reqID = "req-" + newUUID()
	}
	cmdID := strings.TrimSpace(req.CommandID)
	if cmdID == "" {
		cmdID = reqID
	}
	resp := &ExecuteCommandResponse{
		CommandID:      cmdID,
		RequestID:      reqID,
		NodeID:         e.cfg.ServerID,
		Action:         req.Action,
		ExecutedAtUnix: time.Now().Unix(),
	}
	params := req.Parameters
	if params == nil {
		params = map[string]string{}
	}

	// 1. Kiểm tra đúng Node mục tiêu
	if strings.TrimSpace(req.NodeID) != "" && !strings.EqualFold(req.NodeID, e.cfg.ServerID) {
		resp.Status = "REJECTED_UNAUTHORIZED"
		resp.ProtoStatus = pb.CommandResponse_REJECTED_UNAUTHORIZED
		resp.ErrorCode = "NODE_MISMATCH"
		resp.ErrorMessage = fmt.Sprintf("Lệnh nhắm tới node '%s' nhưng agent hiện tại là '%s'", req.NodeID, e.cfg.ServerID)
		resp.DurationMs = time.Since(start).Milliseconds()
		return resp
	}

	// 2. Kiểm tra hạn dùng lệnh
	if req.IssuedAtUnix > 0 && req.DeadlineSeconds > 0 && time.Now().Unix() > req.IssuedAtUnix+req.DeadlineSeconds {
		resp.Status = "REJECTED_DEADLINE_EXCEEDED"
		resp.ProtoStatus = pb.CommandResponse_REJECTED_DEADLINE_EXCEEDED
		resp.ErrorCode = "COMMAND_EXPIRED"
		resp.ErrorMessage = "Lệnh đã quá thời hạn thực thi"
		resp.DurationMs = time.Since(start).Milliseconds()
		return resp
	}

	// 3. Xác thực DelegationToken + Action Authorization + Nonce Replay
	actorScope := strings.TrimSpace(req.ActorID)
	paramsHash := idempotencyParamsHash(req.Action, params)
	if !unsignedTelemetryAllowed(req.Action) || strings.TrimSpace(req.AuthorizationContext) != "" {
		claims, err := gateway.VerifyDelegationToken(e.cfg.DelegationSecret, req.AuthorizationContext, time.Now())
		if err != nil {
			resp.Status = "REJECTED_UNAUTHORIZED"
			resp.ProtoStatus = pb.CommandResponse_REJECTED_UNAUTHORIZED
			resp.ErrorCode = "DELEGATION_INVALID"
			resp.ErrorMessage = err.Error()
			resp.DurationMs = time.Since(start).Milliseconds()
			return resp
		}
		if claims.ParamsSHA256 == "" || claims.RequestID != reqID || actorScope != claims.ActorID || e.replayGuard == nil {
			resp.Status = "REJECTED_UNAUTHORIZED"
			resp.ProtoStatus = pb.CommandResponse_REJECTED_UNAUTHORIZED
			resp.ErrorCode = "DELEGATION_SCOPE_INVALID"
			resp.ErrorMessage = "Delegation requires matching request and actor, parameter digest, and replay protection"
			return resp
		}
		if err := gateway.ActionAuthorizationCheck(claims, e.cfg.ServerID, req.Action, params); err != nil {
			resp.Status = "REJECTED_UNAUTHORIZED"
			resp.ProtoStatus = pb.CommandResponse_REJECTED_UNAUTHORIZED
			resp.ErrorCode = "ACTION_UNAUTHORIZED"
			resp.ErrorMessage = err.Error()
			resp.DurationMs = time.Since(start).Milliseconds()
			return resp
		}
		if e.replayGuard != nil {
			if err := e.replayGuard.ReserveNonce(claims.Nonce, time.Unix(claims.ExpiresAt, 0)); err != nil {
				resp.Status = "REJECTED_UNAUTHORIZED"
				resp.ProtoStatus = pb.CommandResponse_REJECTED_UNAUTHORIZED
				resp.ErrorCode = "REPLAY_DETECTED"
				resp.ErrorMessage = err.Error()
				resp.DurationMs = time.Since(start).Milliseconds()
				return resp
			}
		}
		actorScope = claims.ActorID
	}

	// 4. Kiểm tra Idempotency cache
	if req.IdempotencyKey != "" && e.replayGuard != nil {
		if cached, hit, idempErr := e.replayGuard.CheckIdempotency(actorScope, req.Action, req.IdempotencyKey, paramsHash); idempErr != nil {
			resp.Status = "REJECTED_INVALID_PARAMS"
			resp.ProtoStatus = pb.CommandResponse_REJECTED_INVALID_PARAMS
			resp.ErrorCode = "IDEMPOTENCY_CONFLICT"
			resp.ErrorMessage = idempErr.Error()
			resp.DurationMs = time.Since(start).Milliseconds()
			return resp
		} else if hit && cached != nil {
			resp.Success = true
			resp.Status = "SUCCESS"
			resp.ProtoStatus = pb.CommandResponse_SUCCESS
			resp.IdempotentReplay = true
			resp.Result = cached.Result
			if cached.Result != nil {
				b, _ := json.Marshal(cached.Result)
				resp.ResultPayload = b
			}
			resp.DurationMs = time.Since(start).Milliseconds()
			return resp
		}
	}

	setOK := func(res any) {
		b, _ := json.Marshal(res)
		resp.Success = true
		resp.Status = "SUCCESS"
		resp.ProtoStatus = pb.CommandResponse_SUCCESS
		resp.Result = res
		resp.ResultPayload = b
	}
	setFail := func(statusStr string, pStatus pb.CommandResponse_CommandStatus, code, msg string, res any) {
		resp.Success = false
		resp.Status = statusStr
		resp.ProtoStatus = pStatus
		resp.ErrorCode = code
		resp.ErrorMessage = msg
		if res != nil {
			b, _ := json.Marshal(res)
			resp.Result = res
			resp.ResultPayload = b
		}
	}

	switch req.Action {
	case "INSPECT_HOST":
		setOK(e.hostCol.GetLatest())

	case "INSPECT_GPUS":
		setOK(e.gpuCol.GetLatest())

	case "COLLECT_TELEMETRY":
		snapshot := map[string]any{
			"host":       e.hostCol.GetLatest(),
			"gpu":        e.gpuCol.GetLatest(),
			"processes":  e.procCol.Collect("all"),
			"workspaces": nil,
		}
		if ws, err := e.dockerCtrl.ListWorkspaces(ctx); err == nil {
			snapshot["workspaces"] = ws
		}
		setOK(snapshot)

	case "TERMINATE_PROCESS":
		ref := params["ref"]
		if ref == "" && strings.TrimSpace(e.cfg.DelegationSecret) == "" {
			if pid, err := strconv.Atoi(params["pid"]); err == nil && pid > 0 {
				if pInfo, pErr := e.procCol.GetProcessByPID(pid); pErr == nil && pInfo != nil {
					ref = pInfo.Ref
				}
			}
		}
		if ref == "" {
			setFail("REJECTED_INVALID_PARAMS", pb.CommandResponse_REJECTED_INVALID_PARAMS, "INVALID_PROCESS_REF", "Thiếu tham số ref chuẩn (cần ref có starttime ticks)", nil)
		} else {
			res, termErr := e.procCtrl.TerminateProcess(ref, 10, true)
			if termErr != nil {
				setFail("FAILED", pb.CommandResponse_FAILED, "TERMINATE_FAILED", termErr.Error(), nil)
			} else {
				setOK(res)
			}
		}

	case "PROVISION_DOCKER_WORKSPACE":
		var provReq docker.ProvisionRequest
		if rawJSON, ok := params["spec_json"]; ok && rawJSON != "" {
			if err := json.Unmarshal([]byte(rawJSON), &provReq); err != nil {
				setFail("REJECTED_INVALID_PARAMS", pb.CommandResponse_REJECTED_INVALID_PARAMS, "INVALID_PROVISION_SPEC", fmt.Sprintf("spec_json không hợp lệ: %v", err), nil)
				break
			}
		} else {
			uid, _ := strconv.Atoi(params["uid"])
			gid, _ := strconv.Atoi(params["gid"])
			port, _ := strconv.Atoi(params["ssh_port"])
			cpuCores, _ := strconv.Atoi(params["cpu_limit_cores"])
			cpuWeight, _ := strconv.Atoi(params["cpu_weight"])
			ramMB, _ := strconv.ParseInt(params["ram_limit_mb"], 10, 64)
			pidsLim, _ := strconv.ParseInt(params["pids_limit"], 10, 64)
			gen, _ := strconv.ParseUint(params["generation"], 10, 64)
			owner := params["owner"]
			if owner == "" {
				owner = params["username"]
			}
			var gpuUUIDs []string
			if rawGPUs := strings.TrimSpace(params["gpu_uuids"]); rawGPUs != "" {
				if strings.HasPrefix(rawGPUs, "[") {
					_ = json.Unmarshal([]byte(rawGPUs), &gpuUUIDs)
				} else {
					for _, g := range strings.Split(rawGPUs, ",") {
						if cg := strings.TrimSpace(g); cg != "" {
							gpuUUIDs = append(gpuUUIDs, cg)
						}
					}
				}
			}
			provReq = docker.ProvisionRequest{
				WorkspaceID:        params["workspace_id"],
				Name:               params["name"],
				MemberID:           params["member_id"],
				MemberUsername:     owner,
				UID:                uid,
				GID:                gid,
				ImageRef:           params["image_ref"],
				ImageDigest:        params["image_digest"],
				StorageRoot:        params["storage_root"],
				MemberStorageDir:   params["member_storage_dir"],
				ContainerMountPath: params["container_mount_path"],
				ResourceMode:       params["resource_mode"],
				CPUWeight:          cpuWeight,
				SSHPort:            port,
				VPNBindIP:          params["vpn_bind_ip"],
				CPULimitCores:      cpuCores,
				RAMLimitMB:         ramMB,
				PidsLimit:          pidsLim,
				GPUUUIDs:           gpuUUIDs,
				Generation:         gen,
				NetworkIsolated:    params["network_isolated"] == "true",
			}
		}
		if provReq.AuditActor == "" {
			provReq.AuditActor = actorScope
		}
		if provReq.CorrelationID == "" {
			provReq.CorrelationID = reqID
		}
		if provReq.MemberStorageDir == "" && params["host_dir"] != "" {
			provReq.MemberStorageDir = params["host_dir"]
		}
		rec, provErr := e.dockerCtrl.ProvisionWorkspace(ctx, provReq)
		if provErr != nil {
			setFail("FAILED", pb.CommandResponse_FAILED, "PROVISION_FAILED", provErr.Error(), rec)
		} else {
			setOK(rec)
		}

	case "STOP_DOCKER_WORKSPACE":
		cid := params["container_id"]
		wsID := params["workspace_id"]
		if cid == "" {
			setFail("REJECTED_INVALID_PARAMS", pb.CommandResponse_REJECTED_INVALID_PARAMS, "INVALID_CONTAINER_ID", "Thiếu tham số container_id", nil)
		} else {
			var stopErr error
			if wsID != "" && docker.IsCanonicalContainerID(cid) {
				stopErr = e.dockerCtrl.StopWorkspaceContainer(ctx, wsID, cid, 10)
			} else {
				stopErr = e.dockerCtrl.StopContainer(cid, 10)
			}
			if stopErr != nil {
				setFail("FAILED", pb.CommandResponse_FAILED, "STOP_CONTAINER_FAILED", stopErr.Error(), nil)
			} else {
				setOK(map[string]any{"stopped": cid, "status": "exited"})
			}
		}

	case "ARCHIVE_REMOVE_DOCKER_WORKSPACE":
		cid := params["container_id"]
		wsID := params["workspace_id"]
		if wsID == "" || !docker.IsCanonicalContainerID(cid) {
			setFail("REJECTED_INVALID_PARAMS", pb.CommandResponse_REJECTED_INVALID_PARAMS, "INVALID_CANONICAL_WORKSPACE", "Yêu cầu workspace_id và container_id 64-hex chuẩn", nil)
		} else if remErr := e.dockerCtrl.RemoveStoppedWorkspaceContainer(ctx, wsID, cid); remErr != nil {
			setFail("FAILED", pb.CommandResponse_FAILED, "REMOVE_CONTAINER_FAILED", remErr.Error(), nil)
		} else {
			setOK(map[string]any{"removed": cid, "workspace_id": wsID, "status": "deleted"})
		}

	case "SYNC_SSH_AUTHORIZED_KEYS":
		wsID := params["workspace_id"]
		owner := params["member_username"]
		if owner == "" {
			owner = params["username"]
		}
		var keys []string
		if rawKeys := strings.TrimSpace(params["keys_json"]); rawKeys != "" {
			if err := json.Unmarshal([]byte(rawKeys), &keys); err != nil {
				setFail("REJECTED_INVALID_PARAMS", pb.CommandResponse_REJECTED_INVALID_PARAMS, "INVALID_KEYS_JSON", err.Error(), nil)
				break
			}
		} else if singleKey := strings.TrimSpace(params["public_key"]); singleKey != "" {
			keys = []string{singleKey}
		}
		res, syncErr := e.dockerCtrl.SyncAuthorizedKeys(ctx, wsID, owner, keys)
		if syncErr != nil {
			setFail("FAILED", pb.CommandResponse_FAILED, "SYNC_SSH_KEYS_FAILED", syncErr.Error(), nil)
		} else {
			setOK(res)
		}

	case "VERIFY_DOCKER_WORKSPACE":
		wsID := params["workspace_id"]
		cid := params["container_id"]
		uid, _ := strconv.Atoi(params["uid"])
		gid, _ := strconv.Atoi(params["gid"])
		port, _ := strconv.Atoi(params["ssh_port"])
		mode := params["resource_mode"]
		bindIP := params["vpn_bind_ip"]
		res, verErr := e.dockerCtrl.VerifyWorkspaceRuntime(ctx, wsID, cid, uid, gid, port, mode, bindIP)
		if verErr != nil {
			setFail("FAILED", pb.CommandResponse_FAILED, "VERIFY_WORKSPACE_FAILED", verErr.Error(), res)
		} else {
			setOK(res)
		}

	case "LIST_STORAGE_POOLS":
		// Production tuyệt đối dùng NewSystemQuotaManager(false)
		collector := storage.NewInventoryCollector(e.cfg.ServerID, storage.NewSystemQuotaManager(false))
		setOK(collector.CollectStoragePools())

	case "INSPECT_STORAGE_POOL":
		storageID := params["storage_id"]
		mountPoint := params["mount_point"]
		collector := storage.NewInventoryCollector(e.cfg.ServerID, storage.NewSystemQuotaManager(false))
		var found *storage.StoragePoolInfo
		for _, p := range collector.CollectStoragePools() {
			if (storageID != "" && p.StorageID == storageID) || (mountPoint != "" && p.MountPoint == mountPoint) {
				found = p
				break
			}
		}
		if found == nil {
			setFail("FAILED", pb.CommandResponse_FAILED, "STORAGE_POOL_NOT_FOUND", "Storage pool không tồn tại hoặc không đủ điều kiện", nil)
		} else {
			setOK(found)
		}

	case "VALIDATE_STORAGE_ALLOCATION":
		storageID := params["storage_id"]
		requestedQuotaGiB, _ := strconv.ParseInt(params["requested_quota_gib"], 10, 64)
		qm := storage.NewSystemQuotaManager(false)
		collector := storage.NewInventoryCollector(e.cfg.ServerID, qm)
		provisioner := storage.NewProvisioner(e.cfg.ServerID, collector, qm)
		pool, err := provisioner.ValidateAllocation(storageID, requestedQuotaGiB)
		if err != nil {
			setFail("FAILED", pb.CommandResponse_FAILED, "STORAGE_ALLOCATION_INVALID", err.Error(), nil)
		} else {
			setOK(map[string]any{"valid": true, "storage_id": storageID, "quota_gib": requestedQuotaGiB, "mount_point": pool.MountPoint, "quota_supported": pool.QuotaSupported})
		}

	case "PROVISION_MEMBER_STORAGE":
		var memberReq storage.MemberStorageRequest
		if rawJSON, ok := params["spec_json"]; ok && rawJSON != "" {
			if err := json.Unmarshal([]byte(rawJSON), &memberReq); err != nil {
				setFail("REJECTED_INVALID_PARAMS", pb.CommandResponse_REJECTED_INVALID_PARAMS, "INVALID_STORAGE_SPEC", err.Error(), nil)
				break
			}
		} else {
			uid, _ := strconv.Atoi(params["uid"])
			gid, _ := strconv.Atoi(params["gid"])
			quota, _ := strconv.ParseInt(params["quota_gib"], 10, 64)
			memberReq = storage.MemberStorageRequest{
				MemberID:           params["member_id"],
				MemberUsername:     params["member_username"],
				HostID:             e.cfg.ServerID,
				StorageID:          params["storage_id"],
				StorageQuotaGiB:    quota,
				ContainerMountPath: "/workspace",
				UID:                uid,
				GID:                gid,
				CorrelationID:      reqID,
				AuditActor:         actorScope,
			}
		}
		qm := storage.NewSystemQuotaManager(false)
		collector := storage.NewInventoryCollector(e.cfg.ServerID, qm)
		provisioner := storage.NewProvisioner(e.cfg.ServerID, collector, qm)
		res, err := provisioner.ProvisionMemberStorage(memberReq)
		if err != nil {
			setFail("FAILED", pb.CommandResponse_FAILED, "MEMBER_STORAGE_PROVISION_FAILED", err.Error(), res)
		} else {
			setOK(res)
		}

	case "GET_RESOURCE_PROFILE":
		memMgr := resources.NewMemoryPolicyManager(e.cfg.ServerID, 20.0)
		setOK(memMgr.EvaluateHostProfile())

	default:
		setFail("REJECTED_UNAUTHORIZED", pb.CommandResponse_REJECTED_UNAUTHORIZED, "UNSUPPORTED_ACTION", fmt.Sprintf("Hành động '%s' không được hỗ trợ trên node", req.Action), nil)
	}

	if req.IdempotencyKey != "" && resp.Success && e.replayGuard != nil {
		e.replayGuard.RecordIdempotency(actorScope, req.Action, req.IdempotencyKey, paramsHash, &gateway.DispatchResponse{
			Success: true,
			NodeID:  e.cfg.ServerID,
			Status:  "SUCCESS",
			Result:  resp.Result,
		}, 10*time.Minute)
	}

	resp.DurationMs = time.Since(start).Milliseconds()
	return resp
}

// Idempotency covers workspace configuration. The complete original parameters
// are still checked against the delegation signature on each invocation.
func idempotencyParamsHash(action string, params map[string]string) string {
	if action == "PROVISION_DOCKER_WORKSPACE" {
		var spec map[string]json.RawMessage
		if json.Unmarshal([]byte(params["spec_json"]), &spec) == nil && spec["repository_token"] != nil {
			delete(spec, "repository_token")
			delete(spec, "correlation_id")
			if normalized, err := json.Marshal(spec); err == nil {
				params = maps.Clone(params)
				params["spec_json"] = string(normalized)
			}
		}
	}
	return gateway.ComputeParamsHash(params)
}

func unsignedTelemetryAllowed(action string) bool {
	switch action {
	case "INSPECT_HOST", "INSPECT_GPUS", "COLLECT_TELEMETRY", "LIST_STORAGE_POOLS", "INSPECT_STORAGE_POOL", "GET_RESOURCE_PROFILE":
		return true
	default:
		return false
	}
}
