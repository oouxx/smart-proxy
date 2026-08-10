// Probe Engine: 主动探测代理节点质量。
//
// 作为独立二进制嵌入 Python 项目，通过 HTTP 与 Python 交互。
// 测量指标:
//   - 隧道连接建立耗时 (connect_ms)
//   - 延迟 (多次 HTTP 请求) + 抖动
//   - 丢包率 (请求失败比例)
//   - 下载速度 + 首字节时间
//
// 两种探测后端:
//   - mihomo 隧道 (推荐): 读取订阅文件 config/mihomo.yaml，用 mihomo 库
//     adapter.ParseProxy 解析节点，通过 proxy.DialContext 走真实代理隧道
//     访问目标 (https://probe_url)，测得的延迟/速度才是"实际可用性"。
//   - 直连 (fallback): 节点不在订阅文件中时，对 server:port 直接测量
//     TCP/TLS，仅反映网络可达性。
package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/metacubex/mihomo/adapter"
	C "github.com/metacubex/mihomo/constant"
	"gopkg.in/yaml.v3"
)

// ProbeResult 单次探测结果，与 Python 侧 ProbeResult 对应。
type ProbeResult struct {
	NodeID            string   `json:"node_id"`
	Timestamp         int64    `json:"timestamp"`
	LatencyMS         *float64 `json:"latency_ms"`
	LatencyMinMS      *float64 `json:"latency_min_ms"`
	LatencyMaxMS      *float64 `json:"latency_max_ms"`
	LatencyP50MS      *float64 `json:"latency_p50_ms"`
	LatencyP95MS      *float64 `json:"latency_p95_ms"`
	TCPConnectMS      *float64 `json:"tcp_connect_ms"`
	TLSHandshakeMS    *float64 `json:"tls_handshake_ms"`
	PacketLoss        *float64 `json:"packet_loss"`
	JitterMS          *float64 `json:"jitter_ms"`
	SuccessRate       *float64 `json:"success_rate"`
	Success           bool     `json:"success"`
	DownloadSpeedKbps *float64 `json:"download_speed_kbps"`
	UploadSpeedKbps   *float64 `json:"upload_speed_kbps"`
	FirstByteMS       *float64 `json:"first_byte_ms"`
}

// ProbeRequest 探测请求。
type ProbeRequest struct {
	NodeID         string `json:"node_id"`
	Server         string `json:"server"`
	Port           int    `json:"port"`
	TLS            bool   `json:"tls"`
	ProbeURL       string `json:"probe_url"`
	DownloadURL    string `json:"download_url"`
	UploadURL      string `json:"upload_url"`
	TimeoutMS      int    `json:"timeout_ms"`
	LatencySamples int    `json:"latency_samples"`
	ConfigPath     string `json:"config_path"` // 节点订阅文件路径
}

// ProbeAllRequest 批量探测请求: 一次探测整个节点池。
type ProbeAllRequest struct {
	Nodes          []map[string]any `json:"nodes"`
	ProbeURL       string           `json:"probe_url"`
	DownloadURL    string           `json:"download_url"`
	UploadURL      string           `json:"upload_url"`
	TimeoutMS      int              `json:"timeout_ms"`
	LatencySamples int              `json:"latency_samples"`
	Concurrency    int              `json:"concurrency"`
}

// ProbeAllResponse 批量探测响应。
type ProbeAllResponse struct {
	Results []ProbeResult `json:"results"`
}

// loadedProxies 按节点 name 索引的完整节点配置 (来自订阅文件)。
var loadedProxies map[string]map[string]any

// loadProxies 从订阅文件加载所有节点配置，按 name 索引。
func loadProxies(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var con map[string]any
	if err := yaml.Unmarshal(data, &con); err != nil {
		return err
	}
	list, ok := con["proxies"].([]any)
	if !ok {
		return fmt.Errorf("配置文件 %s 中没有 proxies", path)
	}
	loadedProxies = make(map[string]map[string]any, len(list))
	for _, item := range list {
		if m, ok := item.(map[string]any); ok {
			if name, ok := m["name"].(string); ok {
				loadedProxies[name] = m
			}
		}
	}
	return nil
}

// resolveProbeParams 解析探测公共参数 (超时/采样数/探测URL)。
func resolveProbeParams(req ProbeRequest) (time.Duration, int, string) {
	timeout := time.Duration(req.TimeoutMS) * time.Millisecond
	if timeout <= 0 {
		timeout = 5 * time.Second
	}
	samples := req.LatencySamples
	if samples <= 0 {
		samples = 5
	}
	probeURL := req.ProbeURL
	if probeURL == "" {
		probeURL = "https://www.gstatic.com/generate_204"
	}
	return timeout, samples, probeURL
}

// probe 对单个节点执行完整探测。
func probe(req ProbeRequest) ProbeResult {
	// 优先走 mihomo 隧道探测 (节点在订阅文件中)
	if cfg, ok := loadedProxies[req.NodeID]; ok {
		return probeWithConfig(cfg, req)
	}

	// fallback: 直连探测 server:port
	result := ProbeResult{
		NodeID:    req.NodeID,
		Timestamp: time.Now().Unix(),
	}
	timeout, samples, probeURL := resolveProbeParams(req)
	probeDirect(req, &result, timeout, samples, probeURL)
	return result
}

// probeWithConfig 对给定节点配置执行 mihomo 隧道探测。
func probeWithConfig(cfg map[string]any, req ProbeRequest) ProbeResult {
	result := ProbeResult{
		NodeID:    req.NodeID,
		Timestamp: time.Now().Unix(),
	}
	timeout, samples, probeURL := resolveProbeParams(req)
	probeViaMihomo(cfg, req, &result, timeout, samples, probeURL)
	return result
}

// probeAll 并发遍历整个节点池，对每个节点执行 mihomo 隧道探测。
// 返回与输入顺序一致的结果列表。
func probeAll(req ProbeAllRequest) []ProbeResult {
	results := make([]ProbeResult, len(req.Nodes))
	concurrency := req.Concurrency
	if concurrency <= 0 {
		concurrency = 20
	}
	sem := make(chan struct{}, concurrency)
	var wg sync.WaitGroup
	for i, node := range req.Nodes {
		wg.Add(1)
		sem <- struct{}{}
		go func(i int, node map[string]any) {
			defer wg.Done()
			defer func() { <-sem }()
			name, _ := node["name"].(string)
			pr := ProbeRequest{
				NodeID:         name,
				ProbeURL:       req.ProbeURL,
				DownloadURL:    req.DownloadURL,
				UploadURL:      req.UploadURL,
				TimeoutMS:      req.TimeoutMS,
				LatencySamples: req.LatencySamples,
			}
			results[i] = probeWithConfig(node, pr)
		}(i, node)
	}
	wg.Wait()
	return results
}

// probeViaMihomo 通过 mihomo 库建立真实代理隧道，测量经代理访问目标的指标。
func probeViaMihomo(cfg map[string]any, req ProbeRequest, result *ProbeResult, timeout time.Duration, samples int, probeURL string) {
	proxy, err := adapter.ParseProxy(cfg)
	if err != nil {
		log.Printf("节点 %s 解析失败: %v", req.NodeID, err)
		return // success=false
	}

	u, _ := url.Parse(probeURL)
	dstHost := u.Hostname()
	dstPort := uint16(443)
	if u.Port() != "" {
		if p, err := strconv.Atoi(u.Port()); err == nil {
			dstPort = uint16(p)
		}
	}

	// 1. 隧道连接建立耗时 (一次主动拨号)
	dialStart := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	conn, err := proxy.DialContext(ctx, &C.Metadata{Host: dstHost, DstPort: dstPort})
	cancel()
	if err != nil {
		log.Printf("节点 %s 隧道连接失败: %v", req.NodeID, err)
		return // success=false
	}
	connectMS := float64(time.Since(dialStart).Microseconds()) / 1000.0
	result.TCPConnectMS = &connectMS
	conn.Close()

	// 2. 通过隧道发 HTTP 请求测延迟/抖动/丢包
	transport := &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(addr)
			if err != nil {
				return nil, err
			}
			p, err := strconv.ParseUint(port, 10, 16)
			if err != nil {
				return nil, err
			}
			return proxy.DialContext(ctx, &C.Metadata{Host: host, DstPort: uint16(p)})
		},
		DisableKeepAlives: true,
	}
	client := &http.Client{Transport: transport, Timeout: timeout}

	latencies := measureLatency(client, probeURL, samples)
	if len(latencies) == 0 {
		return // success=false
	}
	stats := computeLatencyStats(latencies, samples)
	result.LatencyMS = ptrFloat(stats.avg)
	result.LatencyMinMS = ptrFloat(stats.min)
	result.LatencyMaxMS = ptrFloat(stats.max)
	result.LatencyP50MS = ptrFloat(stats.p50)
	result.LatencyP95MS = ptrFloat(stats.p95)
	result.PacketLoss = ptrFloat(stats.loss)
	result.JitterMS = ptrFloat(stats.jitter)
	result.SuccessRate = ptrFloat(stats.successRate)

	// 3. 下载速度 + 首字节时间 (经隧道)
	if req.DownloadURL != "" {
		speed, firstByte := measureDownload(client, req.DownloadURL, timeout)
		if speed != nil {
			result.DownloadSpeedKbps = speed
		}
		if firstByte != nil {
			result.FirstByteMS = firstByte
		}
	}

	// 4. 上传速度 (经隧道)
	if req.UploadURL != "" {
		if speed := measureUpload(client, req.UploadURL, timeout); speed != nil {
			result.UploadSpeedKbps = speed
		}
	}

	result.Success = true
}

// probeDirect 对 server:port 直连测量 (fallback 后端)。
func probeDirect(req ProbeRequest, result *ProbeResult, timeout time.Duration, samples int, probeURL string) {
	addr := net.JoinHostPort(req.Server, fmt.Sprintf("%d", req.Port))

	// 1. TCP 连接耗时
	tcpStart := time.Now()
	conn, err := net.DialTimeout("tcp", addr, timeout)
	if err != nil {
		return // success=false
	}
	tcpMS := float64(time.Since(tcpStart).Microseconds()) / 1000.0
	result.TCPConnectMS = &tcpMS

	// 2. TLS 握手耗时 (如果启用)
	if req.TLS {
		tlsStart := time.Now()
		tlsConn := tls.Client(conn, &tls.Config{InsecureSkipVerify: true})
		if err := tlsConn.Handshake(); err != nil {
			conn.Close()
			return
		}
		tlsMS := float64(time.Since(tlsStart).Microseconds()) / 1000.0
		result.TLSHandshakeMS = &tlsMS
		conn = tlsConn
	}
	conn.Close()

	// 3. 延迟 + 抖动 + 丢包 (多次 HTTP 请求)
	latencies := measureLatency(http.DefaultClient, probeURL, samples)
	if len(latencies) == 0 {
		return // success=false
	}
	stats := computeLatencyStats(latencies, samples)
	result.LatencyMS = ptrFloat(stats.avg)
	result.LatencyMinMS = ptrFloat(stats.min)
	result.LatencyMaxMS = ptrFloat(stats.max)
	result.LatencyP50MS = ptrFloat(stats.p50)
	result.LatencyP95MS = ptrFloat(stats.p95)
	result.PacketLoss = ptrFloat(stats.loss)
	result.JitterMS = ptrFloat(stats.jitter)
	result.SuccessRate = ptrFloat(stats.successRate)

	// 4. 下载速度 + 首字节时间
	if req.DownloadURL != "" {
		speed, firstByte := measureDownload(http.DefaultClient, req.DownloadURL, timeout)
		if speed != nil {
			result.DownloadSpeedKbps = speed
		}
		if firstByte != nil {
			result.FirstByteMS = firstByte
		}
	}

	// 5. 上传速度
	if req.UploadURL != "" {
		if speed := measureUpload(http.DefaultClient, req.UploadURL, timeout); speed != nil {
			result.UploadSpeedKbps = speed
		}
	}

	result.Success = true
}

// measureLatency 通过给定 client 发送多次 HTTP 请求测量延迟，返回成功请求的延迟列表。
func measureLatency(client *http.Client, url string, samples int) []float64 {
	latencies := make([]float64, 0, samples)
	var mu sync.Mutex
	var wg sync.WaitGroup

	for i := 0; i < samples; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			start := time.Now()
			resp, err := client.Get(url)
			if err != nil {
				return
			}
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			if resp.StatusCode >= 200 && resp.StatusCode < 400 {
				ms := float64(time.Since(start).Microseconds()) / 1000.0
				mu.Lock()
				latencies = append(latencies, ms)
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	return latencies
}

// measureDownload 通过给定 client 下载测试文件测量速度 (KB/s) 和首字节时间 (ms)。
func measureDownload(client *http.Client, url string, timeout time.Duration) (*float64, *float64) {
	start := time.Now()
	resp, err := client.Get(url)
	if err != nil {
		return nil, nil
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, nil
	}
	firstByte := float64(time.Since(start).Microseconds()) / 1000.0

	// 下载固定时长或固定字节数
	dlStart := time.Now()
	var total int64
	buf := make([]byte, 32*1024)
	for {
		n, err := resp.Body.Read(buf)
		total += int64(n)
		if err != nil {
			break
		}
		if time.Since(dlStart) > 3*time.Second {
			break
		}
	}
	elapsed := time.Since(dlStart).Seconds()
	if elapsed <= 0 {
		return nil, &firstByte
	}
	speedKbps := float64(total) * 8 / 1000 / elapsed
	return &speedKbps, &firstByte
}

func avgFloat(vals []float64) float64 {
	var sum float64
	for _, v := range vals {
		sum += v
	}
	return sum / float64(len(vals))
}

func ptrFloat(v float64) *float64 { return &v }

// latencyStats 延迟统计汇总。
type latencyStats struct {
	avg, min, max, p50, p95, jitter float64
	loss, successRate               float64
}

// computeLatencyStats 从延迟样本计算统计指标。
func computeLatencyStats(latencies []float64, samples int) latencyStats {
	s := latencyStats{}
	if len(latencies) == 0 {
		return s
	}
	s.avg = avgFloat(latencies)
	s.min = latencies[0]
	s.max = latencies[0]
	for _, v := range latencies {
		if v < s.min {
			s.min = v
		}
		if v > s.max {
			s.max = v
		}
	}
	sorted := make([]float64, len(latencies))
	copy(sorted, latencies)
	sort.Float64s(sorted)
	s.p50 = percentile(sorted, 50)
	s.p95 = percentile(sorted, 95)
	s.jitter = jitter(latencies)
	s.loss = float64(samples-len(latencies)) / float64(samples)
	s.successRate = float64(len(latencies)) / float64(samples)
	return s
}

// percentile 计算排序后样本的百分位。
func percentile(sorted []float64, p float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	idx := int(float64(len(sorted)-1) * p / 100.0)
	return sorted[idx]
}

// measureUpload 通过给定 client POST 固定大小数据测量上传速度 (KB/s)。
func measureUpload(client *http.Client, url string, timeout time.Duration) *float64 {
	payload := bytes.Repeat([]byte{0}, 1<<20) // 1MB
	start := time.Now()
	resp, err := client.Post(url, "application/octet-stream", bytes.NewReader(payload))
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil
	}
	elapsed := time.Since(start).Seconds()
	if elapsed <= 0 {
		return nil
	}
	speedKbps := float64(len(payload)) * 8 / 1000 / elapsed
	return &speedKbps
}

// jitter 计算延迟抖动 (平均绝对偏差)。
func jitter(vals []float64) float64 {
	if len(vals) < 2 {
		return 0
	}
	mean := avgFloat(vals)
	var sum float64
	for _, v := range vals {
		diff := v - mean
		if diff < 0 {
			diff = -diff
		}
		sum += diff
	}
	return sum / float64(len(vals))
}

func main() {
	addr := flag.String("addr", "127.0.0.1:9100", "探针 HTTP 服务地址")
	configPath := flag.String("config", "config/mihomo.yaml", "节点订阅文件路径 (用于 mihomo 隧道探测)")
	flag.Parse()

	// 加载节点订阅文件 (失败则退化为纯直连探测)
	if err := loadProxies(*configPath); err != nil {
		log.Printf("加载节点订阅文件 %s 失败，退化为直连探测: %v", *configPath, err)
	} else {
		log.Printf("已加载 %d 个节点配置，启用 mihomo 隧道探测", len(loadedProxies))
	}

	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})

	http.HandleFunc("/probe", func(w http.ResponseWriter, r *http.Request) {
		var req ProbeRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		result := probe(req)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(result)
	})

	http.HandleFunc("/probe_all", func(w http.ResponseWriter, r *http.Request) {
		var req ProbeAllRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		results := probeAll(req)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(ProbeAllResponse{Results: results})
	})

	log.Printf("probe engine listening on %s", *addr)
	if err := http.ListenAndServe(*addr, nil); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
