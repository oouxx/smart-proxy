// Probe Engine: 主动探测代理节点质量。
//
// 作为独立二进制嵌入 Python 项目，通过 HTTP 与 Python 交互。
// 测量指标:
//   - TCP 连接耗时
//   - TLS 握手耗时
//   - 延迟 (多次 HTTP 请求) + 抖动
//   - 丢包率 (请求失败比例)
//   - 下载速度 + 首字节时间
//
// 说明: 对代理服务器 (server:port) 直接测量 TCP/TLS 反映节点可达性。
// 通过代理转发 (vmess/vless/trojan/ss) 的完整测量需集成 mihomo 内核。
package main

import (
	"crypto/tls"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"sync"
	"time"
)

// ProbeResult 单次探测结果，与 Python 侧 ProbeResult 对应。
type ProbeResult struct {
	NodeID            string   `json:"node_id"`
	Timestamp         int64    `json:"timestamp"`
	LatencyMS         *float64 `json:"latency_ms"`
	TCPConnectMS      *float64 `json:"tcp_connect_ms"`
	TLSHandshakeMS    *float64 `json:"tls_handshake_ms"`
	PacketLoss        *float64 `json:"packet_loss"`
	JitterMS          *float64 `json:"jitter_ms"`
	Success           bool     `json:"success"`
	DownloadSpeedKbps *float64 `json:"download_speed_kbps"`
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
	TimeoutMS      int    `json:"timeout_ms"`
	LatencySamples int    `json:"latency_samples"`
}

// probe 对单个节点执行完整探测。
func probe(req ProbeRequest) ProbeResult {
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

	addr := net.JoinHostPort(req.Server, fmt.Sprintf("%d", req.Port))
	result := ProbeResult{
		NodeID:    req.NodeID,
		Timestamp: time.Now().Unix(),
	}

	// 1. TCP 连接耗时
	tcpStart := time.Now()
	conn, err := net.DialTimeout("tcp", addr, timeout)
	if err != nil {
		result.Success = false
		return result
	}
	tcpMS := float64(time.Since(tcpStart).Microseconds()) / 1000.0
	result.TCPConnectMS = &tcpMS

	// 2. TLS 握手耗时 (如果启用)
	if req.TLS {
		tlsStart := time.Now()
		tlsConn := tls.Client(conn, &tls.Config{InsecureSkipVerify: true})
		if err := tlsConn.Handshake(); err != nil {
			conn.Close()
			result.Success = false
			return result
		}
		tlsMS := float64(time.Since(tlsStart).Microseconds()) / 1000.0
		result.TLSHandshakeMS = &tlsMS
		conn = tlsConn
	}
	conn.Close()

	// 3. 延迟 + 抖动 + 丢包 (多次 HTTP 请求)
	latencies := measureLatency(probeURL, timeout, samples)
	if len(latencies) == 0 {
		result.Success = false
		return result
	}
	loss := float64(samples-len(latencies)) / float64(samples)
	result.PacketLoss = &loss

	avg := avgFloat(latencies)
	result.LatencyMS = &avg
	jitter := jitter(latencies)
	result.JitterMS = &jitter

	// 4. 下载速度 + 首字节时间
	if req.DownloadURL != "" {
		speed, firstByte := measureDownload(req.DownloadURL, timeout)
		if speed != nil {
			result.DownloadSpeedKbps = speed
		}
		if firstByte != nil {
			result.FirstByteMS = firstByte
		}
	}

	result.Success = true
	return result
}

// measureLatency 发送多次 HTTP 请求测量延迟，返回成功请求的延迟列表。
func measureLatency(url string, timeout time.Duration, samples int) []float64 {
	client := &http.Client{Timeout: timeout}
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

// measureDownload 下载测试文件测量速度 (KB/s) 和首字节时间 (ms)。
func measureDownload(url string, timeout time.Duration) (*float64, *float64) {
	client := &http.Client{Timeout: timeout}
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
	flag.Parse()

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

	log.Printf("probe engine listening on %s", *addr)
	if err := http.ListenAndServe(*addr, nil); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
