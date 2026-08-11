"""Bandit Learner: 在线学习 (多臂老虎机)。

Phase 3 在线学习: 用多臂老虎机 (Multi-Armed Bandit) 在"探索"与"利用"之间
平衡，持续从真实反馈中学习哪个节点实际表现最好，而不是只依赖静态评分。

实现:
- UCB1: 上置信界算法，天然平衡探索/利用
- epsilon-greedy: 以一定概率随机探索
- 支持持久化 (保存/加载统计)，跨会话累积学习
"""
from __future__ import annotations

import json
import logging
import math
import random
from pathlib import Path

logger = logging.getLogger(__name__)


class BanditLearner:
    """基于 UCB1 + epsilon-greedy 的多臂老虎机学习器。

    每个节点是一个"臂" (arm)。维护:
    - _counts: 每个臂被选择的次数
    - _values: 每个臂的累计奖励均值

    select() 返回按 UCB 分数排序的 Top K 臂，用于选择器在候选节点中
    做探索/利用决策。
    """

    def __init__(
        self,
        alpha: float = 1.0,
        exploration_rate: float = 0.1,
        min_selections: int = 5,
        seed: int | None = None,
    ) -> None:
        self.alpha = alpha
        self.exploration_rate = exploration_rate
        self.min_selections = min_selections
        self._counts: dict[str, int] = {}
        self._values: dict[str, float] = {}
        self._rng = random.Random(seed)

    # ---------- 统计 ----------

    def _total_selections(self) -> int:
        return sum(self._counts.values())

    def ucb_score(self, arm_id: str) -> float:
        """计算单个臂的 UCB1 分数。"""
        n = self._counts.get(arm_id, 0)
        if n == 0:
            # 未探索过的臂给最高优先级 (鼓励探索)
            return float("inf")
        total = self._total_selections()
        value = self._values.get(arm_id, 0.0)
        # UCB1: value + alpha * sqrt(2 * ln(total) / n)
        exploration = self.alpha * math.sqrt(2.0 * math.log(max(total, 1)) / n)
        return value + exploration

    def select(self, arm_ids: list[str], top_k: int = 1) -> list[str]:
        """从候选臂中选择 Top K。

        优先选择未探索或选择次数不足 min_selections 的臂 (鼓励探索阶段)，
        全部充分探索后以 exploration_rate 概率做 epsilon-greedy 随机探索，
        其余按 UCB1 分数选择。
        """
        if not arm_ids:
            return []
        # 未探索或采样不足 min_selections 的臂优先 (鼓励探索)
        under_selected = [
            a for a in arm_ids if self._counts.get(a, 0) < self.min_selections
        ]
        if under_selected:
            self._rng.shuffle(under_selected)
            return under_selected[:top_k]

        if self._rng.random() < self.exploration_rate:
            # 随机探索
            return self._rng.sample(arm_ids, min(top_k, len(arm_ids)))

        ranked = sorted(arm_ids, key=self.ucb_score, reverse=True)
        return ranked[:top_k]

    def update(self, arm_id: str, reward: float) -> None:
        """用真实反馈更新臂的统计 (增量均值)。"""
        reward = max(0.0, min(1.0, reward))
        n = self._counts.get(arm_id, 0)
        old_value = self._values.get(arm_id, 0.0)
        self._counts[arm_id] = n + 1
        self._values[arm_id] = old_value + (reward - old_value) / (n + 1)

    def forget(self, arm_id: str) -> None:
        """移除一个臂的统计 (节点被淘汰时)。"""
        self._counts.pop(arm_id, None)
        self._values.pop(arm_id, None)

    # ---------- 持久化 ----------

    def save(self, path: str | Path) -> None:
        """保存学习状态到 JSON。"""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "alpha": self.alpha,
            "exploration_rate": self.exploration_rate,
            "min_selections": self.min_selections,
            "counts": self._counts,
            "values": self._values,
        }
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        logger.info("Bandit 状态已保存: %s", out)

    def load(self, path: str | Path) -> None:
        """加载学习状态。"""
        p = Path(path)
        if not p.exists():
            logger.info("Bandit 状态不存在: %s，从零开始", p)
            return
        data = json.loads(p.read_text())
        self.alpha = data.get("alpha", self.alpha)
        self.exploration_rate = data.get("exploration_rate", self.exploration_rate)
        self.min_selections = data.get("min_selections", self.min_selections)
        self._counts = {str(k): int(v) for k, v in data.get("counts", {}).items()}
        self._values = {str(k): float(v) for k, v in data.get("values", {}).items()}
        logger.info("Bandit 状态已加载: %s (%d 个臂)", p, len(self._counts))
