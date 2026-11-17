package aiotgit

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const maxJSONResponse = 1 << 20

var (
	workspaceIDRE = regexp.MustCompile(`^ws-[a-f0-9]{10}$`)
	requestIDRE   = regexp.MustCompile(`^req-[a-f0-9]{12}$`)
	sha256RE      = regexp.MustCompile(`^[a-f0-9]{64}$`)
	commitRE      = regexp.MustCompile(`^[a-f0-9]{40}([a-f0-9]{24})?$`)
)

type RequestInfo struct {
	ID              string `json:"id"`
	RepoURL         string `json:"repo_url"`
	RequestedRef    string `json:"requested_ref"`
	CommitSHA       string `json:"commit_sha"`
	State           string `json:"state"`
	GitBundleDigest string `json:"git_bundle_digest"`
	Error           string `json:"error"`
}

type createRequest struct {
	RepoURL         string `json:"repo_url"`
	Ref             string `json:"ref"`
	TargetWorkspace string `json:"target_workspace"`
	IdempotencyKey  string `json:"idempotency_key"`
}

type Client struct {
	baseURL   string
	token     string
	workspace string
	HTTP      *http.Client
	PollEvery time.Duration
	WaitLimit time.Duration
}

func NewClient(baseURL, token string, httpClient *http.Client) (*Client, error) {
	parsed, err := url.Parse(strings.TrimSpace(baseURL))
	if err != nil || parsed == nil || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("AIOTGIT broker URL is invalid")
	}
	if parsed.Scheme != "https" {
		ip := net.ParseIP(parsed.Hostname())
		if parsed.Scheme != "http" || ip == nil || !ip.IsLoopback() {
			return nil, errors.New("AIOTGIT broker requires HTTPS except for loopback tests")
		}
	}
	if !strings.HasSuffix(strings.TrimRight(parsed.Path, "/"), "/api/v1/repositories") {
		return nil, errors.New("AIOTGIT broker URL must end with /api/v1/repositories")
	}
	workspace, err := workspaceFromToken(token)
	if err != nil {
		return nil, err
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 2 * time.Minute, Transport: &http.Transport{Proxy: nil}}
	} else {
		copy := *httpClient
		httpClient = &copy
		if httpClient.Timeout == 0 {
			httpClient.Timeout = 2 * time.Minute
		}
		if httpClient.Transport == nil {
			httpClient.Transport = &http.Transport{Proxy: nil}
		}
	}
	httpClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return &Client{
		baseURL: strings.TrimRight(parsed.String(), "/"), token: token, workspace: workspace,
		HTTP: httpClient, PollEvery: 1500 * time.Millisecond, WaitLimit: 10 * time.Minute,
	}, nil
}

func workspaceFromToken(token string) (string, error) {
	if len(token) > 16<<10 || strings.Count(token, ".") != 1 {
		return "", errors.New("AIOTGIT scoped token is invalid")
	}
	parts := strings.SplitN(token, ".", 2)
	payload, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil || len(payload) == 0 || len(payload) > 8192 {
		return "", errors.New("AIOTGIT scoped token is invalid")
	}
	var claims struct {
		Audience    string `json:"aud"`
		WorkspaceID string `json:"workspace_id"`
	}
	// The CLI reads only this routing envelope. The Broker verifies the token
	// signature and all authorization claims before acting on it.
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(payload, &raw); err != nil {
		return "", errors.New("AIOTGIT scoped token payload is invalid")
	}
	if value := raw["aud"]; json.Unmarshal(value, &claims.Audience) != nil {
		return "", errors.New("AIOTGIT scoped token audience is invalid")
	}
	if value := raw["workspace_id"]; json.Unmarshal(value, &claims.WorkspaceID) != nil {
		return "", errors.New("AIOTGIT scoped token workspace is invalid")
	}
	if claims.Audience != "labops-repositoryguard" || !workspaceIDRE.MatchString(claims.WorkspaceID) {
		return "", errors.New("AIOTGIT scoped token is not bound to a workspace")
	}
	return claims.WorkspaceID, nil
}

func (c *Client) Submit(ctx context.Context, repoURL, ref, idempotencyKey string) (*RequestInfo, error) {
	if strings.TrimSpace(ref) == "" {
		ref = "HEAD"
	}
	payload := createRequest{RepoURL: repoURL, Ref: ref, TargetWorkspace: c.workspace, IdempotencyKey: idempotencyKey}
	var result RequestInfo
	if err := c.jsonRequest(ctx, http.MethodPost, "cli/requests", payload, http.StatusAccepted, &result); err != nil {
		return nil, err
	}
	if !requestIDRE.MatchString(result.ID) || result.State == "" {
		return nil, errors.New("Broker returned an invalid repository request")
	}
	return &result, nil
}

func (c *Client) List(ctx context.Context) ([]RequestInfo, error) {
	var result []RequestInfo
	if err := c.jsonRequest(ctx, http.MethodGet, "cli/requests", nil, http.StatusOK, &result); err != nil {
		return nil, err
	}
	for _, item := range result {
		if !requestIDRE.MatchString(item.ID) {
			return nil, errors.New("Broker returned an invalid repository request ID")
		}
	}
	return result, nil
}

func (c *Client) Get(ctx context.Context, requestID string) (*RequestInfo, error) {
	if !requestIDRE.MatchString(requestID) {
		return nil, errors.New("repository request ID is invalid")
	}
	var result RequestInfo
	if err := c.jsonRequest(ctx, http.MethodGet, "cli/requests/"+requestID, nil, http.StatusOK, &result); err != nil {
		return nil, err
	}
	if result.ID != requestID || result.State == "" {
		return nil, errors.New("Broker returned a mismatched repository request")
	}
	return &result, nil
}

func (c *Client) Bundle(ctx context.Context, requestID string) (io.ReadCloser, string, string, error) {
	if !requestIDRE.MatchString(requestID) {
		return nil, "", "", errors.New("repository request ID is invalid")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/cli/requests/"+requestID+"/bundle", nil)
	if err != nil {
		return nil, "", "", err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Accept", "application/vnd.git-bundle")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, "", "", fmt.Errorf("AIOTGIT Broker unavailable: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		defer resp.Body.Close()
		return nil, "", "", responseError(resp)
	}
	digest, commit := resp.Header.Get("X-LabOps-Artifact-SHA256"), resp.Header.Get("X-LabOps-Commit-SHA")
	if !sha256RE.MatchString(digest) || !commitRE.MatchString(commit) {
		resp.Body.Close()
		return nil, "", "", errors.New("Broker returned invalid approved bundle metadata")
	}
	return resp.Body, digest, commit, nil
}

func (c *Client) jsonRequest(ctx context.Context, method, path string, body any, expectedStatus int, output any) error {
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+"/"+path, reader)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return fmt.Errorf("AIOTGIT Broker unavailable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != expectedStatus {
		return responseError(resp)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxJSONResponse+1))
	if err != nil {
		return fmt.Errorf("read Broker response: %w", err)
	}
	if len(data) > maxJSONResponse {
		return errors.New("Broker JSON response exceeded the size limit")
	}
	if output != nil {
		decoder := json.NewDecoder(bytes.NewReader(data))
		if err := decoder.Decode(output); err != nil {
			return fmt.Errorf("decode Broker response: %w", err)
		}
		if err := decoder.Decode(new(any)); !errors.Is(err, io.EOF) {
			return errors.New("Broker response contains trailing data")
		}
	}
	return nil
}

func responseError(resp *http.Response) error {
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	if err != nil {
		return fmt.Errorf("AIOTGIT Broker returned HTTP %d", resp.StatusCode)
	}
	var parsed struct {
		Detail string `json:"detail"`
		Error  string `json:"error"`
	}
	if json.Unmarshal(body, &parsed) == nil {
		if parsed.Detail != "" {
			return fmt.Errorf("AIOTGIT Broker returned HTTP %d: %s", resp.StatusCode, parsed.Detail)
		}
		if parsed.Error != "" {
			return fmt.Errorf("AIOTGIT Broker returned HTTP %d: %s", resp.StatusCode, parsed.Error)
		}
	}
	return fmt.Errorf("AIOTGIT Broker returned HTTP %d", resp.StatusCode)
}
