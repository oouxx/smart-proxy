"""Selector Service: 根据评分选择 Top N 节点并控制 mihomo 切换。"""
from __future__ import annotations

import logging

from ..config import SelectorConfig
from .mihomo import MihomoController
from .node_manager import NodeManager

logger = logging.getLogger(__name__)


class SelectorService:
    """最终节点选择。"""

    def __init__(
        self,
        cfg: SelectorConfig,
        nodes: NodeManager,
        mihomo: MihomoController,
    ) -> None:
        self.cfg = cfg
        self.nodes = nodes
        self.mihomo = mihomo

    def select_top(self, scores: dict[str, float]) -> list[str]:
        """根据评分选择 Top N 节点。"""
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return [node_id for node_id, score in ranked if score >= self.cfg.min_score][
            : self.cfg.top_n
        ]

    async def apply(
        self,
        scores: dict[str, float],
        group: str | None = None,
        force: str | None = None,
    ) -> list[str]:
        """选择 Top N 节点并切换 mihomo 当前节点。

        group 缺省时使用配置中的 selector.group。
        force: 强制切换到的节点 (由 Bandit 在线学习决定)，
               仅在候选 Top 内有效，否则回退到最高分节点。
        """
        group = group or self.cfg.group
        top = self.select_top(scores)
        if not top:
            logger.warning("没有满足最低评分的节点")
            return []
        best = force if force and force in top else top[0]
        logger.info("切换节点 %s -> %s (Top: %s)", group, best, top)
        await self.mihomo.switch_proxy(group, best)
        return top
