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

    async def apply(self, group: str, scores: dict[str, float]) -> list[str]:
        """选择 Top N 节点并切换 mihomo 当前节点。"""
        top = self.select_top(scores)
        if not top:
            logger.warning("没有满足最低评分的节点")
            return []
        best = top[0]
        logger.info("切换节点 %s -> %s (Top: %s)", group, best, top)
        await self.mihomo.switch_proxy(group, best)
        return top
