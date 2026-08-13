"""配置管理: 加载 YAML 配置并校验。"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar, cast

import yaml

T = TypeVar("T")


@dataclass
class ProbeConfig:
    """探针引擎配置。"""

    interval_sec: int = 60          # 活跃节点周期探测间隔
    fast_interval_sec: int = 10     # 新节点快速探测间隔
    timeout_ms: int = 5000
    concurrency: int = 20
    probe_url: str = ""                       # 兼容字段: 单个探测目标 (建议用 probe_urls)
    probe_urls: list[str] = field(default_factory=lambda: [  # 多个探测目标站点 (综合打分)
        "https://www.gstatic.com/generate_204",
        "https://www.google.com/generate_204",
        "https://www.youtube.com",
        "https://github.com",
        "https://www.cloudflare.com",
        "https://www.wikipedia.org",
        "https://www.microsoft.com",
    ])
    download_urls: list[str] = field(default_factory=list)  # 下载测速 URL 列表 (留空则跳过)
    upload_urls: list[str] = field(default_factory=list)    # 上传测速 URL 列表 (留空则跳过)
    latency_samples: int = 5         # 延迟采样次数
    engine_binary: str = "bin/probe-engine"  # Go 探针二进制路径
    engine_addr: str = "127.0.0.1:9100"     # Go 探针 HTTP 服务地址
    engine_config: str = "config/mihomo.yaml"  # 节点订阅文件路径 (mihomo 隧道探测)
    model_dir: str = ""                      # smart 模型目录 (含 Model.bin 与 smart_weight_data.csv)
    use_model: bool = True                    # 是否加载 Model.bin 打分 (False 则用 CalculateWeight 启发式)
    collect_csv: bool = False                 # 是否采集训练数据 (探针覆盖全部节点, 写 smart_weight_data.csv)
    # 节点健康状态机阈值 (快速更替场景可调小)
    bad_after_failures: int = 3          # 连续失败多少次降为 BAD
    dead_after_failures: int = 10        # 连续失败多少次降为 DEAD (可被淘汰)
    recovery_successes: int = 3          # 分阶段恢复: 连续成功多少次回 ACTIVE


@dataclass
class ModelConfig:
    """机器学习模型配置。"""

    model_path: str = "models/ranker.lgb"
    feature_window_min: int = 30
    model_weight: float = 0.7       # 模型评分权重
    realtime_weight: float = 0.3    # 实时评分权重
    auto_train: bool = False        # collect 是否自动重训模型
    auto_train_interval_hours: int = 72  # 自动重训间隔 (小时)
    min_train_samples: int = 10     # 自动重训最低样本数 (快速更替下应调高，避免噪声)


@dataclass
class BanditConfig:
    """在线学习 (Bandit) 配置。"""

    alpha: float = 1.0                 # UCB1 探索系数
    exploration_rate: float = 0.1      # epsilon-greedy 随机探索概率
    min_selections: int = 5            # 最少选择次数 (预留)
    state_path: str = "models/bandit_state.json"  # 学习状态持久化路径
    reward_window_min: int = 30         # 计算奖励的历史窗口 (分钟)


@dataclass
class CollectConfig:
    """定时批处理采集配置。"""

    output: str = "data/features.csv"  # 特征数据 CSV 输出路径 (每小时追加)
    retention_days: int = 14            # 特征数据保留期 (天)，超过则清理
    schedule: str = "0 * * * *"         # cron 表达式，serve 内定时跑 collect
    # 自动训练 smart 模型 (探针采集的 smart_weight_data.csv -> Model.bin)
    smart_auto_train: bool = False      # collect 完成后自动重训 smart 模型
    smart_min_samples: int = 1000       # 最少训练样本数 (需 probe.collect_csv 采集到足够数据)
    smart_interval_hours: int = 24      # 自动重训间隔 (小时)


@dataclass
class NodeSourceConfig:
    """节点源配置 (批处理生成 mihomo-smart.yaml)。"""

    path: str = "config/mihomo.yaml"   # 本地订阅文件
    url: str = ""                       # 远程订阅 URL (优先于 path)
    top_n: int = 50                     # 输出节点数量
    min_score: float = 0.0              # 最低评分过滤
    output: str = "config/mihomo-smart.yaml"  # 输出文件
    refresh_interval_sec: int = 300     # serve 节点源同步间隔 (快速更替下调小)


@dataclass
class Config:
    """全局配置。"""

    probe: ProbeConfig = field(default_factory=ProbeConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    node_source: NodeSourceConfig = field(default_factory=NodeSourceConfig)
    bandit: BanditConfig = field(default_factory=BanditConfig)
    collect: CollectConfig = field(default_factory=CollectConfig)

    @staticmethod
    def _sub(raw: dict, cls: type[T]) -> T:
        """从 dict 构造子配置，过滤掉非 dataclass 字段 (容忍拼错/多余 key)。"""
        valid = {f.name for f in fields(cast(Any, cls))}
        return cast(T, cls(**{k: v for k, v in raw.items() if k in valid}))

    @classmethod
    def load(cls, path: str | Path) -> Config:
        """从 YAML 文件加载配置。"""
        path = Path(path)
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text()) or {}
        return cls(
            probe=cls._sub(raw.get("probe", {}), ProbeConfig),
            model=cls._sub(raw.get("model", {}), ModelConfig),
            node_source=cls._sub(raw.get("node_source", {}), NodeSourceConfig),
            bandit=cls._sub(raw.get("bandit", {}), BanditConfig),
            collect=cls._sub(raw.get("collect", {}), CollectConfig),
        )
