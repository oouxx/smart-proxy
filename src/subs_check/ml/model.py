"""Intelligence Engine: LightGBM Ranker + 实时评分。

最终评分:
    final_score = model_weight * model_score + realtime_weight * realtime_score
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np

from ..config import ModelConfig
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
        X = np.array(
            [[f.get(name, 0.0) for name in self.features.feature_names()] for f in feature_dicts]
        )
        scores = self._model.predict(X)
        return [float(np.clip(s, 0.0, 1.0)) for s in scores]

    def train(self, X, y) -> None:
        """训练模型 (占位，实际使用 LightGBM Ranker)。"""
        try:
            import lightgbm as lgb
        except ImportError:
            logger.warning("lightgbm 未安装，跳过训练")
            return
        model = lgb.LGBMRanker(
            objective="lambdarank",
            n_estimators=100,
            learning_rate=0.1,
        )
        model.fit(X, y, group=[len(X)])
        self._model = model
        Path(self.cfg.model_path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, self.cfg.model_path)
        logger.info("模型已保存: %s", self.cfg.model_path)


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
