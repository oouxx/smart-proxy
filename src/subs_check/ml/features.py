"""Feature Engine: 将原始探测数据转换为模型特征。

支持:
- 时间窗口特征 (均值/标准差/趋势)
- 节点属性特征 (国家/协议)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.node_manager import ProxyNode
from ..core.probe import ProbeResult


class FeatureEngine:
    """从探测历史构建特征向量。"""

    def build_features(
        self,
        node: ProxyNode,
        history: list[ProbeResult],
    ) -> dict[str, float]:
        """为单个节点构建特征。"""
        if not history:
            return self._empty_features(node)

        latencies = [r.latency_ms for r in history if r.latency_ms is not None]
        losses = [r.packet_loss for r in history if r.packet_loss is not None]
        speeds = [r.download_speed_kbps for r in history if r.download_speed_kbps is not None]
        successes = [1.0 if r.success else 0.0 for r in history]

        feats: dict[str, float] = {
            "latency_avg_5m": float(np.mean(latencies)) if latencies else -1.0,
            "latency_std_5m": float(np.std(latencies)) if len(latencies) > 1 else 0.0,
            "loss_rate_30m": float(np.mean(losses)) if losses else 0.0,
            "speed_avg_10m": float(np.mean(speeds)) if speeds else 0.0,
            "success_rate": float(np.mean(successes)),
            "failure_count": float(len(history) - sum(successes)),
            "time_hour": float(pd.Timestamp.now().hour),
            "country": self._country_code(node.country),
            "protocol": self._protocol_code(node.protocol),
        }
        return feats

    def _empty_features(self, node: ProxyNode) -> dict[str, float]:
        return {
            "latency_avg_5m": -1.0,
            "latency_std_5m": 0.0,
            "loss_rate_30m": 0.0,
            "speed_avg_10m": 0.0,
            "success_rate": 0.0,
            "failure_count": 0.0,
            "time_hour": float(pd.Timestamp.now().hour),
            "country": self._country_code(node.country),
            "protocol": self._protocol_code(node.protocol),
        }

    @staticmethod
    def _country_code(country: str) -> float:
        # 简单哈希编码，生产环境应使用 one-hot 或 embedding
        return float(hash(country) % 1000) if country else 0.0

    @staticmethod
    def _protocol_code(protocol: str) -> float:
        mapping = {"vmess": 1.0, "vless": 2.0, "trojan": 3.0, "ss": 4.0, "ssr": 5.0, "hysteria": 6.0}
        return mapping.get(protocol.lower(), 0.0)

    def feature_names(self) -> list[str]:
        return [
            "latency_avg_5m",
            "latency_std_5m",
            "loss_rate_30m",
            "speed_avg_10m",
            "success_rate",
            "failure_count",
            "time_hour",
            "country",
            "protocol",
        ]
