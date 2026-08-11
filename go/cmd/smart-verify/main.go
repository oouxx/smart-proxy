package main

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/metacubex/mihomo/component/smart/lightgbm"
	"github.com/vernesong/leaves"
)

// 验证训练脚本生成的模型能被 mihomo smart 组件加载并预测。
// 用法: go run smart_verify.go <Model.bin>
func main() {
	if len(os.Args) < 2 {
		fmt.Println("用法: go run smart_verify.go <Model.bin>")
		os.Exit(1)
	}
	modelPath, _ := filepath.Abs(os.Args[1])

	// 1. 解析内嵌 transforms 段
	ft, err := lightgbm.LoadTransformsFromModel(modelPath)
	if err != nil {
		fmt.Printf("解析 transforms 失败: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("transforms 解析 OK: enabled=%v, 特征数=%d, 变换组=%d\n",
		ft.TransformsEnabled, len(ft.FeatureOrder), len(ft.Transforms))
	if err := ft.ValidateTransforms(lightgbm.MaxFeatureSize); err != nil {
		fmt.Printf("transforms 校验失败: %v\n", err)
		os.Exit(1)
	}
	fmt.Println("transforms 校验 OK")

	// 2. 用 leaves 解析模型树
	model, err := leaves.LGEnsembleFromFile(modelPath, false)
	if err != nil {
		fmt.Printf("加载模型树失败: %v\n", err)
		os.Exit(1)
	}
	fmt.Println("模型树加载 OK")

	// 3. 构造 30 个原始特征 (与 prepareFeatures 输出一致)
	features := []float64{
		10, 1,          // success, failure
		4.615, 4.394,   // connect_time, latency (log1p)
		1.792, 2.398,   // upload_mb, history_upload_mb (log1p)
		6.908, 6.908,   // maxuploadrate_kb, history_maxuploadrate_kb (log1p)
		3.045, 3.738,   // download_mb, history_download_mb (log1p)
		8.517, 8.517,   // maxdownloadrate_kb, history_maxdownloadrate_kb (log1p)
		1.099, 1.386,   // duration_minutes, history_duration_minutes (log1p)
		0,              // last_used_seconds (log1p)
		0, 1,           // is_udp, is_tcp
		0.01, 0.01,     // loss_rate, cumul_loss_rate
		0, 0, 0, 443,   // asn_feature, country_feature, address_feature, port_feature
		0, 0,           // traffic_ratio, traffic_density
		0,              // connection_type_feature
		0, 0, 0, 0,     // asn_hash, host_hash, ip_hash, geoip_hash
	}
	if len(features) != lightgbm.MaxFeatureSize {
		fmt.Printf("特征数错误: %d != %d\n", len(features), lightgbm.MaxFeatureSize)
		os.Exit(1)
	}

	// 4. 应用 transforms
	scaled := ft.ApplyTransforms(features)

	// 5. 预测
	pred := model.PredictSingle(scaled, 0)
	fmt.Printf("预测权重: %.4f\n", pred)
}
