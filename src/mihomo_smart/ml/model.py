"""Intelligence Engine: LightGBM Ranker + 实时评分。

最终评分:
    final_score = model_weight * model_score + realtime_weight * realtime_score
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from ..config import ModelConfig
from ..core.node_manager import ProxyNode
from ..core.probe import ProbeResult
from .features import FeatureEngine

logger = logging.getLogger(__name__)


class NodeRanker:
    """LightGBM 排序模型封装。"""

    def __init__(self, cfg: ModelConfig, features: FeatureEngine) -> None:
        self.cfg = cfg
        self.features = features
        self._model = None

    def load(self) -> None:
        """加载已训练模型。"""
        path = Path(self.cfg.model_path)
        if path.exists():
            self._model = joblib.load(path)
            logger.info("加载模型: %s", path)
        else:
            logger.warning("模型不存在: %s，将使用实时评分", path)

    def predict(self, feature_dicts: list[dict[str, float]]) -> list[float]:
        """预测节点质量分数 (0~1)。"""
        if self._model is None:
            return [0.5] * len(feature_dicts)
        X = pd.DataFrame(
            [
                [f.get(name, 0.0) for name in self.features.feature_names()]
                for f in feature_dicts
            ],
            columns=self.features.feature_names(),
        )
        scores = self._model.predict(X)
        return [float(np.clip(s, 0.0, 1.0)) for s in scores]

    def train(self, X, y, feature_names=None) -> None:
        """训练 LightGBM 回归模型 (预测节点质量分数 0~1)。

        X: 特征矩阵 (n_samples, n_features)
        y: 质量标签 (0~1)
        feature_names: 特征名列表 (用于特征重要性)
        """
        try:
            import lightgbm as lgb
        except ImportError:
            logger.warning("lightgbm 未安装，跳过训练")
            return
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
        model.fit(X, y, feature_name=feature_names)
        self._model = model
        Path(self.cfg.model_path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, self.cfg.model_path)
        logger.info("模型已保存: %s", self.cfg.model_path)


def quality_label(result: ProbeResult) -> float:
    """根据单次探测结果计算 0~1 质量标签 (训练目标)。

    基于延迟/成功率/速度综合打分，作为 LightGBM 回归的训练标签。
    """
    if not result.success:
        return 0.0
    score = 0.0
    if result.latency_ms is not None:
        # 延迟越低越好，200ms 以下满分 (与 RealtimeScorer/_bandit_reward 一致)
        score += 0.5 * max(0.0, 1.0 - result.latency_ms / 200.0)
    if result.success_rate is not None:
        score += 0.3 * result.success_rate
    if result.download_speed_kbps is not None:
        # 速度越高越好，10Mbps 以上满分
        score += 0.2 * min(1.0, result.download_speed_kbps / 10_000.0)
    return float(np.clip(score, 0.0, 1.0))


class RealtimeScorer:
    """实时评分，补充机器学习结果。"""

    def score(
        self,
        latency_ms: float | None,
        speed_kbps: float | None,
        success_rate: float,
        failure_count: int,
    ) -> float:
        """实时评分 (0~1)。"""
        score = 0.0
        if latency_ms is not None:
            # 延迟越低越好，200ms 以下满分
            score += 0.4 * max(0.0, 1.0 - latency_ms / 200.0)
        if speed_kbps is not None:
            # 速度越高越好，10Mbps 以上满分
            score += 0.3 * min(1.0, speed_kbps / 10_000.0)
        score += 0.2 * success_rate
        score -= 0.1 * min(1.0, failure_count / 10.0)
        return float(np.clip(score, 0.0, 1.0))


class IntelligenceEngine:
    """综合模型评分与实时评分。"""

    def __init__(self, cfg: ModelConfig, ranker: NodeRanker, scorer: RealtimeScorer) -> None:
        self.cfg = cfg
        self.ranker = ranker
        self.scorer = scorer

    def final_score(self, model_score: float, realtime_score: float) -> float:
        return (
            self.cfg.model_weight * model_score + self.cfg.realtime_weight * realtime_score
        )

    def score_node(self, node: ProxyNode, history: list[ProbeResult]) -> float:
        """对单个节点计算最终评分 (0~1)。

        完整链路: 探测历史 -> 特征 -> 模型预测 (LightGBM)
                  + 实时评分 -> 按权重融合。
        """
        feats = self.ranker.features.build_features(node, history)
        model_score = self.ranker.predict([feats])[0]

        if not history:
            # 无历史数据: 仅用属性特征得出的模型分 + 空实时分
            return self.final_score(model_score, 0.0)

        # 实时分: 取历史窗口的聚合指标
        latencies = [r.latency_ms for r in history if r.latency_ms is not None]
        speeds = [r.download_speed_kbps for r in history if r.download_speed_kbps is not None]
        success_count = sum(1 for r in history if r.success)
        latest = history[-1]
        realtime_score = self.scorer.score(
            latency_ms=float(np.mean(latencies)) if latencies else latest.latency_ms,
            speed_kbps=float(np.mean(speeds)) if speeds else latest.download_speed_kbps,
            success_rate=success_count / len(history),
            failure_count=len(history) - success_count,
        )
        return self.final_score(model_score, realtime_score)
