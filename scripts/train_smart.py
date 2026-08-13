#!/usr/bin/env python3
"""从 mihomo smart 组件采集的训练数据训练 LightGBM 模型 (CLI 入口)。

实际逻辑在 mihomo_smart.ml.smart_train, 这里仅做命令行封装。

用法:
    python scripts/train_smart.py --csv <smart_weight_data.csv> --output <Model.bin>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mihomo_smart.ml import smart_train


def main() -> None:
    parser = argparse.ArgumentParser(description="训练 mihomo smart LightGBM 模型")
    parser.add_argument("--csv", nargs="+", required=True,
                        help="smart_weight_data.csv 路径 (可传多个，探针采集+真实流量模拟会合并)")
    parser.add_argument("--output", default="Model.bin", help="输出模型路径")
    parser.add_argument("--min-samples", type=int, default=1000,
                        help="最少样本数 (默认 1000)")
    args = parser.parse_args()

    try:
        ok, msg = smart_train.train_model(args.csv, args.output, args.min_samples)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"加载样本: {msg}")
    if not ok:
        print(f"训练未完成: {msg}", file=sys.stderr)
        sys.exit(1)

    print(f"模型已保存: {Path(args.output)}")
    print(f"特征数: {len(smart_train.FEATURES)}")
    print(f"StandardScaler 特征: {len(smart_train.STD_FEATURES)} 个")
    print(f"RobustScaler 特征: {len(smart_train.ROBUST_FEATURES)} 个")


if __name__ == "__main__":
    main()
