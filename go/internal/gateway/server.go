package gateway

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/pki"
	pb "github.com/Hieuslecong/labops_control_center/go/proto/labops/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/peer"
)

// Server đại diện cho LabOps Go Agent Gateway
type Server struct {
	pb.UnimplementedNodeAgentGatewayServiceServer
	sessions          *SessionManager
	pkiManager        *pki.CACertificate
	revocationManager *pki.RevocationManager
	grpcServer        *grpc.Server
	listener          net.Listener
	logger            *slog.Logger
	bindAddr          string
	port              int
}

// Config chứa tham số khởi tạo Gateway
type Config struct {
	BindAddr          string
	GRPCPort          int
	HTTPPort          int
	CADir             string
	RevocationManager *pki.RevocationManager
	Logger            *slog.Logger
}

// NewServer khởi tạo instance của Gateway Server
func NewServer(cfg *Config, ca *pki.CACertificate, tlsCfg *tls.Config) (*Server, error) {
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}
	if cfg.RevocationManager == nil {
		cfg.RevocationManager = pki.NewRevocationManager()
	}

	creds := credentials.NewTLS(tlsCfg)
	grpcSrv := grpc.NewServer(
		grpc.Creds(creds),
		grpc.MaxRecvMsgSize(32*1024*1024),
		grpc.MaxSendMsgSize(32*1024*1024),
	)

	s := &Server{
		sessions:          NewSessionManager(),
		pkiManager:        ca,
		revocationManager: cfg.RevocationManager,
		grpcServer:        grpcSrv,
		logger:            cfg.Logger,
		bindAddr:          cfg.BindAddr,
		port:              cfg.GRPCPort,
	}

	pb.RegisterNodeAgentGatewayServiceServer(grpcSrv, s)
	return s, nil
}

// Serve nhận listener đã mở sẵn và phục vụ gRPC mTLS
func (s *Server) Serve(lis net.Listener) error {
	s.listener = lis
	s.logger.Info("🛡️ LabOps Agent Gateway (gRPC mTLS) đang phục vụ", "address", lis.Addr().String())
	return s.grpcServer.Serve(lis)
}

// Start khởi chạy gRPC mTLS server
func (s *Server) Start() error {
	addr := fmt.Sprintf("%s:%d", s.bindAddr, s.port)
	lis, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("không thể lắng nghe cổng gRPC Gateway %s: %w", addr, err)
	}
	s.listener = lis
	s.logger.Info("🛡️ LabOps Agent Gateway (gRPC mTLS) đang lắng nghe", "address", addr)

	return s.grpcServer.Serve(lis)
}

// Stop dừng an toàn server
func (s *Server) Stop() {
	if s.grpcServer != nil {
		s.grpcServer.GracefulStop()
	}
}

// SessionManager trả về quản lý phiên để HTTP API sử dụng
func (s *Server) SessionManager() *SessionManager {
	return s.sessions
}

// Tunnel xử lý kết nối stream 2 chiều từ Node Agent
func (s *Server) Tunnel(stream pb.NodeAgentGatewayService_TunnelServer) error {
	ctx := stream.Context()

	// 1. Xác thực danh tính mTLS từ TLS Peer Certificate
	p, ok := peer.FromContext(ctx)
	if !ok || p == nil || p.AuthInfo == nil {
		s.logger.Warn("Từ chối kết nối gRPC: Không tìm thấy thông tin xác thực TLS Peer")
		return errors.New("mTLS: thiếu thông tin peer certificate")
	}

	tlsInfo, ok := p.AuthInfo.(credentials.TLSInfo)
	if !ok || len(tlsInfo.State.VerifiedChains) == 0 || len(tlsInfo.State.VerifiedChains[0]) == 0 {
		s.logger.Warn("Từ chối kết nối gRPC: Chuỗi chứng chỉ client mTLS chưa được xác minh")
		return errors.New("mTLS: client certificate không hợp lệ hoặc không được tin cậy")
	}

	peerCert := tlsInfo.State.VerifiedChains[0][0]
	certNodeID, err := pki.ExtractNodeIDFromCert(peerCert)
	if err != nil {
		s.logger.Warn("Từ chối kết nối gRPC: Không thể trích xuất Node ID từ certificate", "err", err)
		return fmt.Errorf("mTLS: không thể nhận diện Node ID: %w", err)
	}

	if !s.revocationManager.IsNodeAllowed(certNodeID) {
		s.logger.Warn("Từ chối kết nối gRPC: Node ID đã bị thu hồi hoặc từ chối", "node_id", certNodeID)
		return fmt.Errorf("mTLS: node %s đã bị thu hồi quyền truy cập", certNodeID)
	}

	// 2. Chờ nhận bản tin đầu tiên NodeHello
	firstMsg, err := stream.Recv()
	if err != nil {
		if err == io.EOF {
			return nil
		}
		return fmt.Errorf("lỗi nhận bản tin mở đầu: %w", err)
	}

	hello := firstMsg.GetHello()
	if hello == nil {
		s.logger.Warn("Bản tin mở đầu không phải là NodeHello", "node_id", certNodeID)
		return errors.New("bản tin mở đầu bắt buộc phải là NodeHello")
	}

	// Xác nhận Node ID khai báo trùng khớp tuyệt đối với Node ID trong SAN chứng chỉ
	if hello.NodeId != certNodeID {
		s.logger.Error("Phát hiện giả mạo danh tính: Node ID khai báo khác với chứng chỉ client",
			"claimed", hello.NodeId,
			"cert", certNodeID,
		)
		return errors.New("giả mạo danh tính: Node ID không khớp với Client Certificate")
	}

	s.logger.Info("✅ Node Agent đã kết nối thành công qua mTLS TLS 1.3",
		"node_id", hello.NodeId,
		"hostname", hello.Hostname,
		"gpus", hello.GpuCount,
		"agent_version", hello.AgentVersion,
		"remote_addr", p.Addr.String(),
	)

	// Đăng ký Session
	session := s.sessions.RegisterSession(hello, stream)
	defer s.sessions.RemoveSession(hello.NodeId)

	// Gửi phản hồi NodeRegistration
	actionsList := make([]string, 0, len(AllowedActions))
	for act := range AllowedActions {
		actionsList = append(actionsList, act)
	}

	ackMsg := &pb.GatewayMessage{
		MessageId:     newUUID(),
		TimestampUnix: time.Now().Unix(),
		Payload: &pb.GatewayMessage_RegistrationAck{
			RegistrationAck: &pb.NodeRegistration{
				Approved:                 true,
				NodeId:                   hello.NodeId,
				ClusterId:                "labops-production-v2",
				HeartbeatIntervalSeconds: 5,
				TelemetryIntervalSeconds: 5,
				Message:                  "Chào mừng bạn đến với LabOps Zero-Trust Multi-Node Mesh",
				AllowedActions:           actionsList,
			},
		},
	}

	session.streamMu.Lock()
	err = stream.Send(ackMsg)
	session.streamMu.Unlock()
	if err != nil {
		s.logger.Error("Không thể gửi RegistrationAck tới Node", "node_id", hello.NodeId, "err", err)
		return err
	}

	// 3. Vòng lặp nhận dữ liệu liên tục từ Agent
	for {
		msg, err := stream.Recv()
		if err != nil {
			if errors.Is(err, io.EOF) {
				s.logger.Info("Node Agent đã đóng luồng kết nối", "node_id", hello.NodeId)
				return nil
			}
			s.logger.Warn("Ngắt kết nối đột ngột từ Node Agent", "node_id", hello.NodeId, "err", err)
			return err
		}

		s.handleIncomingAgentMessage(hello.NodeId, session, msg)
	}
}

func (s *Server) handleIncomingAgentMessage(nodeID string, sess *NodeSession, msg *pb.AgentMessage) {
	if msg == nil {
		return
	}

	switch payload := msg.Payload.(type) {
	case *pb.AgentMessage_Heartbeat:
		hb := payload.Heartbeat
		s.sessions.UpdateHeartbeat(nodeID, hb)

		// Gửi HeartbeatAck
		ack := &pb.GatewayMessage{
			MessageId:     newUUID(),
			TimestampUnix: time.Now().Unix(),
			Payload: &pb.GatewayMessage_HeartbeatAck{
				HeartbeatAck: &pb.HeartbeatAck{
					SequenceNumber:         hb.SequenceNumber,
					ServerTimeUnix:         time.Now().Unix(),
					RequestTelemetryResync: false,
				},
			},
		}
		sess.streamMu.Lock()
		_ = sess.stream.Send(ack)
		sess.streamMu.Unlock()

	case *pb.AgentMessage_HostTelemetry:
		s.sessions.UpdateHostTelemetry(nodeID, payload.HostTelemetry)

	case *pb.AgentMessage_GpuTelemetry:
		s.sessions.UpdateGPUTelemetry(nodeID, payload.GpuTelemetry)

	case *pb.AgentMessage_ProcessTelemetry:
		s.sessions.UpdateProcessTelemetry(nodeID, payload.ProcessTelemetry)

	case *pb.AgentMessage_WorkspaceTelemetry:
		s.sessions.UpdateWorkspaceTelemetry(nodeID, payload.WorkspaceTelemetry)

	case *pb.AgentMessage_CommandResponse:
		s.sessions.RouteCommandResponse(payload.CommandResponse)

	case *pb.AgentMessage_AgentError:
		s.logger.Warn("Agent báo cáo lỗi",
			"node_id", nodeID,
			"code", payload.AgentError.Code,
			"message", payload.AgentError.Message,
		)
	}
}

// ExtractPeerCertificate hỗ trợ kiểm thử và tiện ích
func ExtractPeerCertificate(p *peer.Peer) (*x509.Certificate, error) {
	if p == nil || p.AuthInfo == nil {
		return nil, errors.New("không có auth info")
	}
	tlsInfo, ok := p.AuthInfo.(credentials.TLSInfo)
	if !ok || len(tlsInfo.State.VerifiedChains) == 0 || len(tlsInfo.State.VerifiedChains[0]) == 0 {
		return nil, errors.New("chứng chỉ peer không hợp lệ")
	}
	return tlsInfo.State.VerifiedChains[0][0], nil
}
