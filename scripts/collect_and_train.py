#!/usr/bin/env python3
"""一键流程: 用自己的探针采集训练数据 -> 训练自己的 Model.bin。

背景:
    不用 vernesong/mihomo 的预训练模型, 参考其 smart 组件的特征/权重体系,
    用自己的探针引擎采集数据并训练自己的模型。探针引擎会并发对所有节点建
    真实隧道探测, 覆盖全部节点 (而非 smart 组只覆盖被选中的节点)。

流程:
    1. 用探针引擎 probe_all 探测全部节点 (需 config 里 collect_csv: true)
       -> 写出 <model_dir>/smart_weight_data.csv (mihomo smart 30 特征格式,
          权重标签 = CalculateWeight 启发式)
    2. 可选: 与 simulate_traffic.py 采集的真实流量 CSV 合并
    3. 用 train_smart.py 训练 LightGBM -> <model_dir>/Model.bin
    4. 之后探针引擎用 -use-model=true 加载该模型打分 (即用自己训练的模型)

用法:
    python scripts/collect_and_train.py [--config config/config.yaml]
                                        [--extra-csv 真实流量采集的CSV]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="探针采集 + 训练自己的 smart 模型")
    parser.add_argument("--config", "-c", default="config/config.yaml", help="配置文件")
    parser.add_argument("--extra-csv", nargs="*", default=[],
                        help="额外的训练数据 CSV (如 simulate_traffic 采集的真实流量数据)")
    parser.add_argument("--min-samples", type=int, default=1000, help="最少训练样本")
    args = parser.parse_args()

    # 1. 读取配置, 确定 model_dir
    from mihomo_smart.config import Config
    cfg = Config.load(args.config)
    model_dir = cfg.probe.model_dir
    if not model_dir:
        print("错误: 请在配置 probe.model_dir 指定模型目录 (存放 smart_weight_data.csv 和 Model.bin)",
              file=sys.stderr)
        sys.exit(1)
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    csv_path = model_dir / "smart_weight_data.csv"

    # 2. 用探针采集 (collect 命令内部 probe_all, collect_csv 需在配置中开启)
    print(f"==> 用探针引擎探测全部节点并采集训练数据 -> {csv_path}")
    if not cfg.probe.collect_csv:
        print("    警告: 配置 probe.collect_csv=false, 不会采集数据。请先在配置中开启。",
              file=sys.stderr)
    ret = subprocess.run(
        ["uv", "run", "mihomo-smart", "--config", args.config, "collect"],
        check=False,
    )
    if ret.returncode != 0:
        print("探针采集失败", file=sys.stderr)
        sys.exit(ret.returncode)

    if not csv_path.exists() or csv_path.stat().st_size == 0:
        print("错误: 未生成训练数据, 请检查节点源与 probe.collect_csv 配置", file=sys.stderr)
        sys.exit(1)

    # 3. 合并额外数据 (真实流量模拟采集的 CSV)
    inputs = [str(csv_path)] + list(args.extra_csv)
    if len(inputs) > 1:
        print(f"==> 合并训练数据: {inputs}")
        out = csv_path.with_name("smart_weight_data_merged.csv")
        _merge_csv(inputs, out)
        inputs = [str(out)]

    # 4. 训练自己的模型
    output = model_dir / "Model.bin"
    print(f"==> 训练 LightGBM 模型 -> {output}")
    ret = subprocess.run([
        sys.executable, "scripts/train_smart.py",
        "--csv", *inputs,
        "--output", str(output),
        "--min-samples", str(args.min_samples),
    ], check=False)
    if ret.returncode != 0:
        print("训练失败", file=sys.stderr)
        sys.exit(ret.returncode)

    print(f"\n完成! 已生成自己的模型: {output}")
    print("探针引擎将用该模型打分 (probe.use_model 需为 true, model_dir 指向本目录)。")


def _merge_csv(inputs: list[str], out: Path) -> None:
    """合并多个同构 CSV (保留表头), 简单去重 (按完整行)。"""
    import csv
    seen: set = set()
    header: list[str] = []
    rows: list[list] = []
    for path in inputs:
        with open(path, newline="") as f:
            r = csv.reader(f)
            for i, row in enumerate(r):
                if i == 0:
                    if not header:
                        header = row
                    continue
                key = tuple(row)
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"    合并完成: {len(rows)} 行 -> {out}")


if __name__ == "__main__":
    main()
