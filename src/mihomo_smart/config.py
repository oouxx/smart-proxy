"""配置管理: 加载 YAML 配置并校验。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ProbeConfig:
    """探针引擎配置。"""

    interval_sec: int = 60          # 活跃节点周期探测间隔
    fast_interval_sec: int = 10     # 新节点快速探测间隔
    timeout_ms: int = 5000
    concurrency: int = 20
    probe_url: str = "https://www.gstatic.com/generate_204"
    download_url: str = ""          # 下载测速 URL，为空则跳过测速
    upload_url: str = ""            # 上传测速 URL，为空则跳过测速
    latency_samples: int = 5         # 延迟采样次数
    engine_binary: str = "bin/probe-engine"  # Go 探针二进制路径
    engine_addr: str = "127.0.0.1:9100"     # Go 探针 HTTP 服务地址
    engine_config: str = "config/mihomo.yaml"  # 节点订阅文件路径 (mihomo 隧道探测)


@dataclass
class ModelConfig:
    """机器学习模型配置。"""

    model_path: str = "models/ranker.lgb"
    feature_window_min: int = 30
    model_weight: float = 0.7       # 模型评分权重
    realtime_weight: float = 0.3    # 实时评分权重
    auto_train: bool = False        # collect 是否自动重训模型
    auto_train_interval_hours: int = 72  # 自动重训间隔 (小时)


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


@dataclass
class NodeSourceConfig:
    """节点源配置 (批处理生成 mihomo-smart.yaml)。"""

    path: str = "config/mihomo.yaml"   # 本地订阅文件
    url: str = ""                       # 远程订阅 URL (优先于 path)
    top_n: int = 50                     # 输出节点数量
    min_score: float = 0.0              # 最低评分过滤
    output: str = "config/mihomo-smart.yaml"  # 输出文件


@dataclass
class Config:
    """全局配置。"""

    probe: ProbeConfig = field(default_factory=ProbeConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    node_source: NodeSourceConfig = field(default_factory=NodeSourceConfig)
    bandit: BanditConfig = field(default_factory=BanditConfig)
    collect: CollectConfig = field(default_factory=CollectConfig)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        """从 YAML 文件加载配置。"""
        path = Path(path)
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text()) or {}
        return cls(
            probe=ProbeConfig(**raw.get("probe", {})),
            model=ModelConfig(**raw.get("model", {})),
            node_source=NodeSourceConfig(**raw.get("node_source", {})),
            bandit=BanditConfig(**raw.get("bandit", {})),
            collect=CollectConfig(**raw.get("collect", {})),
        )
