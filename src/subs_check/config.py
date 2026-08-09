"""配置管理: 加载 YAML 配置并校验。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class MihomoConfig:
    """mihomo 内核配置。"""

    binary: str = "bin/mihomo"
    config_path: str = "config/mihomo.yaml"
    external_controller: str = "127.0.0.1:9090"
    secret: str = ""
    log_level: str = "info"


@dataclass
class ProbeConfig:
    """探针引擎配置。"""

    interval_sec: int = 60          # 活跃节点周期探测间隔
    fast_interval_sec: int = 10     # 新节点快速探测间隔
    timeout_ms: int = 5000
    concurrency: int = 20
    probe_url: str = "https://www.gstatic.com/generate_204"


@dataclass
class ModelConfig:
    """机器学习模型配置。"""

    model_path: str = "models/ranker.lgb"
    feature_window_min: int = 30
    model_weight: float = 0.7       # 模型评分权重
    realtime_weight: float = 0.3    # 实时评分权重


@dataclass
class SelectorConfig:
    """选择器配置。"""

    top_n: int = 3
    min_score: float = 0.5


@dataclass
class Config:
    """全局配置。"""

    mihomo: MihomoConfig = field(default_factory=MihomoConfig)
    probe: ProbeConfig = field(default_factory=ProbeConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    selector: SelectorConfig = field(default_factory=SelectorConfig)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """从 YAML 文件加载配置。"""
        path = Path(path)
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text()) or {}
        return cls(
            mihomo=MihomoConfig(**raw.get("mihomo", {})),
            probe=ProbeConfig(**raw.get("probe", {})),
            model=ModelConfig(**raw.get("model", {})),
            selector=SelectorConfig(**raw.get("selector", {})),
        )
