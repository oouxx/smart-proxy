"""smart 模型训练 (可被 serve/collect 自动调用, 也可被 scripts/train_smart.py 调用)。

从 mihomo smart 组件采集的训练数据 (smart_weight_data.csv, 30 特征 + 权重) 训练
LightGBM 模型, 生成带 [transforms] 段的 Model.bin (mihomo 加载时自动应用缩放)。
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# 特征顺序 (与 mihomo prepareFeatures 一致)
FEATURES = [
    "success", "failure", "connect_time", "latency",
    "upload_mb", "history_upload_mb", "maxuploadrate_kb", "history_maxuploadrate_kb",
    "download_mb", "history_download_mb", "maxdownloadrate_kb", "history_maxdownloadrate_kb",
    "duration_minutes", "history_duration_minutes", "last_used_seconds",
    "is_udp", "is_tcp", "loss_rate", "cumul_loss_rate",
    "asn_feature", "country_feature", "address_feature", "port_feature",
    "traffic_ratio", "traffic_density", "connection_type_feature",
    "asn_hash", "host_hash", "ip_hash", "geoip_hash",
]

# StandardScaler 作用的特征索引 (与 transform.go 的 std_features 一致)
STD_FEATURES = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 23, 24]
# RobustScaler 作用的特征索引 (与 transform.go 的 robust_features 一致)
ROBUST_FEATURES = [0, 1]
# 不做变换的特征 (untransformed_features)
UNTRANSFORMED = [
    "14:last_used_seconds", "15:is_udp", "16:is_tcp", "17:loss_rate",
    "18:cumul_loss_rate", "19:asn_feature", "20:country_feature",
    "21:address_feature", "22:port_feature", "25:connection_type_feature",
    "26:asn_hash", "27:host_hash", "28:ip_hash", "29:geoip_hash",
]


def load_data(csv_paths: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """读取一个或多个 CSV，返回 (特征矩阵, 权重标签)。

    支持传多个文件 (例如探针采集 + 真实流量模拟采集)，合并后去重训练。
    """
    X: list[list[float]] = []
    y: list[float] = []
    for csv_path in csv_paths:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    feats = [float(row[name]) for name in FEATURES]
                    weight = float(row["weight"])
                except (ValueError, KeyError):
                    continue
                X.append(feats)
                y.append(weight)
    if not X:
        raise ValueError(f"CSV 中没有有效样本: {csv_paths}")
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float)


def compute_scalers(X: np.ndarray) -> tuple[dict, dict]:
    """计算 StandardScaler 和 RobustScaler 参数。"""
    std_mean = X[:, STD_FEATURES].mean(axis=0)
    std_scale = X[:, STD_FEATURES].std(axis=0)
    std_scale[std_scale == 0] = 1.0  # 避免除零

    robust_center = np.median(X[:, ROBUST_FEATURES], axis=0)
    q1 = np.percentile(X[:, ROBUST_FEATURES], 25, axis=0)
    q3 = np.percentile(X[:, ROBUST_FEATURES], 75, axis=0)
    robust_scale = q3 - q1
    robust_scale[robust_scale == 0] = 1.0

    return (
        {"mean": std_mean, "scale": std_scale},
        {"center": robust_center, "scale": robust_scale},
    )


def apply_scalers(X: np.ndarray, std: dict, robust: dict) -> np.ndarray:
    """对特征应用缩放 (与 mihomo ApplyTransforms 一致)。"""
    X = X.copy()
    X[:, STD_FEATURES] = (X[:, STD_FEATURES] - std["mean"]) / std["scale"]
    X[:, ROBUST_FEATURES] = (X[:, ROBUST_FEATURES] - robust["center"]) / robust["scale"]
    return X


def build_transforms_section(std: dict, robust: dict) -> str:
    """生成 mihomo 模型文件末尾的 [transforms] 段。"""
    def fmt(vals: np.ndarray) -> str:
        return ",".join(f"{v:.6f}" for v in vals)

    lines = [
        "[transforms]",
        "[order]",
    ]
    for i, name in enumerate(FEATURES):
        lines.append(f"{i}={name}")
    lines += [
        "[/order]",
        "",
        "[definitions]",
        "std_type=StandardScaler",
        "std_features=" + ",".join(str(i) for i in STD_FEATURES),
        "std_mean=" + fmt(std["mean"]),
        "std_scale=" + fmt(std["scale"]),
        "robust_type=RobustScaler",
        "robust_features=" + ",".join(str(i) for i in ROBUST_FEATURES),
        "robust_center=" + fmt(robust["center"]),
        "robust_scale=" + fmt(robust["scale"]),
        "[/definitions]",
        "",
        "untransformed_features=" + ",".join(UNTRANSFORMED),
        "transform=true",
        "[/transforms]",
    ]
    return "\n".join(lines)


def train_model(
    csv_paths: list[str],
    output: str | Path,
    min_samples: int = 1000,
) -> tuple[bool, str]:
    """从采集数据训练 smart LightGBM 模型并保存。

    返回 (是否成功, 说明)。样本不足或 lightgbm 缺失时不抛异常, 返回 (False, 原因)。
    """
    try:
        import lightgbm as lgb
    except ImportError:
        logger.warning("lightgbm 未安装，跳过 smart 模型训练")
        return False, "lightgbm 未安装"

    X, y = load_data(csv_paths)
    if len(y) < min_samples:
        logger.info("smart 训练样本不足 (%d < %d)，跳过", len(y), min_samples)
        return False, f"样本不足 ({len(y)} < {min_samples})"

    # 计算并应用缩放
    std, robust = compute_scalers(X)
    X_scaled = apply_scalers(X, std, robust)

    # 训练 LightGBM 回归
    model = lgb.LGBMRegressor(
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=5,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbose=-1,
    )
    model.fit(X_scaled, y, feature_name=FEATURES)

    # 保存模型 (文本格式) + 追加 transforms 段
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(out, num_iteration=model.best_iteration_ or model.n_estimators)
    with open(out, "a") as f:
        f.write("\n" + build_transforms_section(std, robust) + "\n")

    logger.info("smart 模型已训练并保存: %s (%d 个样本)", out, len(y))
    return True, f"已保存 {len(y)} 个样本到 {out}"


def should_retrain(model_path: str | Path, interval_hours: int) -> bool:
    """判断是否需要重训 smart 模型 (模型不存在或超过间隔)。"""
    p = Path(model_path)
    if not p.exists():
        return True
    age_hours = (__import__("time").time() - p.stat().st_mtime) / 3600
    return age_hours >= interval_hours
