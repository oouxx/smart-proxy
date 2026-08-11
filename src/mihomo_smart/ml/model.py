"""Intelligence Engine: LightGBM Ranker + 实时评分。

最终评分:
    final_score = model_weight * model_score + realtime_weight * realtime_score
"""
from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any

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
        """预测节点质量分数 (0~1)。

        对模型缺失/特征不匹配等异常做防御: 记录警告并回落 0.5，
        避免已训练模型与当前特征集不一致时崩溃探针循环 (仅用实时评分兜底)。
        """
        if self._model is None:
            return [0.5] * len(feature_dicts)
        X = pd.DataFrame(
            [
                [f.get(name, 0.0) for name in self.features.feature_names()]
                for f in feature_dicts
            ],
            columns=self.features.feature_names(),
        )
        try:
            scores = np.asarray(self._model.predict(X))
        except Exception as exc:  # noqa: BLE001 - 特征不匹配等预测异常
            logger.warning("模型预测失败，回退实时评分: %s", exc)
            return [0.5] * len(feature_dicts)
        return [float(np.clip(s, 0.0, 1.0)) for s in scores]

    def train(
        self,
        X: Any,
        y: Any,
        feature_names: list[str] | None = None,
    ) -> None:
        """训练 LightGBM 回归模型 (预测节点质量分数 0~1)。

        X: 特征矩阵 (LightGBM 兼容的 ndarray/DataFrame/list)
        y: 质量标签 (0~1)
        feature_names: 特征名列表 (用于特征重要性)；为 None 时由 LightGBM
                       从 DataFrame 列名自动推断。
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
        # feature_name 不接受 None (只接受 list 或 "auto")
        if feature_names is not None:
            model.fit(X, y, feature_name=feature_names)
        else:
            model.fit(X, y)
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
    """实时评分，补充机器学习结果。

    含智能惩罚: 连续失败越多惩罚越大 (非线性)，失败越严重降权越明显。
    """

    def score(
        self,
        latency_ms: float | None,
        speed_kbps: float | None,
        success_rate: float,
        failure_count: int,
        consecutive_failures: int = 0,
    ) -> float:
        """实时评分 (0~1)。

        权重和 = 1.0: 延迟 0.4 + 速度 0.3 + 成功率 0.3，与 quality_label
        的权重分配保持一致，使完美节点可达 1.0。
        """
        score = 0.0
        if latency_ms is not None:
            # 延迟越低越好，200ms 以下满分
            score += 0.4 * max(0.0, 1.0 - latency_ms / 200.0)
        if speed_kbps is not None:
            # 速度越高越好，10Mbps 以上满分
            score += 0.3 * min(1.0, speed_kbps / 10_000.0)
        score += 0.3 * success_rate
        # 智能惩罚: 连续失败越多惩罚越大 (非线性)，失败越严重降权越明显
        penalty = 0.15 * (1.0 - math.exp(-consecutive_failures / 2.0))
        score -= penalty
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
                  + 实时评分 -> 按权重融合 -> 时间衰减。

        特性:
        - EMA 平滑: 实时指标用指数移动平均，近期数据权重更高
        - 智能惩罚: 连续失败越多，实时分惩罚越大 (非线性)
        - 时间衰减: 长时间未探测的节点降权
        - 时间窗口: 仅使用最近 feature_window_min 分钟内的探测记录
        """
        history = self._windowed(history)
        feats = self.ranker.features.build_features(node, history)
        model_score = self.ranker.predict([feats])[0]

        if not history:
            # 无历史数据: 仅用属性特征得出的模型分 + 空实时分
            return self.final_score(model_score, 0.0)

        # 实时分: 用 EMA 平滑历史窗口的聚合指标 (近期权重更高)
        latencies = [r.latency_ms for r in history if r.latency_ms is not None]
        speeds = [r.download_speed_kbps for r in history if r.download_speed_kbps is not None]
        success_count = sum(1 for r in history if r.success)
        latest = history[-1]
        # 连续失败次数 (从最近往前的连续失败)
        consecutive_failures = 0
        for r in reversed(history):
            if r.success:
                break
            consecutive_failures += 1

        realtime_score = self.scorer.score(
            latency_ms=self._ema(latencies) if latencies else latest.latency_ms,
            speed_kbps=self._ema(speeds) if speeds else latest.download_speed_kbps,
            success_rate=success_count / len(history),
            failure_count=len(history) - success_count,
            consecutive_failures=consecutive_failures,
        )
        score = self.final_score(model_score, realtime_score)
        # 时间衰减: 长时间未探测的节点降权
        return score * self._time_decay(history)

    def _windowed(self, history: list[ProbeResult]) -> list[ProbeResult]:
        """按 feature_window_min 过滤历史记录；窗口 <=0 表示不限窗口。"""
        window = self.cfg.feature_window_min * 60
        if window <= 0:
            return history
        cutoff = time.time() - window
        return [r for r in history if r.timestamp >= cutoff]

    @staticmethod
    def _ema(values: list[float], alpha: float = 0.5) -> float:
        """指数移动平均 (近期数据权重更高，平滑异常波动)。"""
        if not values:
            return 0.0
        ema = values[0]
        for v in values[1:]:
            ema = alpha * v + (1 - alpha) * ema
        return ema

    @staticmethod
    def _time_decay(history: list[ProbeResult]) -> float:
        """时间衰减因子: 超过 7 天未探测的节点权重逐渐降低。"""
        if not history:
            return 1.0
        last_ts = history[-1].timestamp
        days = (time.time() - last_ts) / 86400.0
        decay_days = 7.0
        if days <= decay_days:
            return 1.0
        # λ=1/30: 超过 7 天后每 30 天衰减约 e 倍
        return math.exp(-(days - decay_days) / 30.0)
