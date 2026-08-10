"""Dynamic Pool: 动态节点池管理。

Phase 3 动态节点池: 让节点池随真实表现动态伸缩。
- 注入: 从节点源 (本地/远程) 拉取新节点加入池
- 淘汰: 连续失败/低分节点降级 BAD -> DEAD 并移出池
- 修剪: 定期清理长期无用的节点，控制池规模
"""
from __future__ import annotations

import logging
import time

from ..config import PoolConfig
from .node_manager import NodeManager, NodeStatus, ProxyNode
from .node_source import load_nodes

logger = logging.getLogger(__name__)


class DynamicPool:
    """动态节点池。"""

    def __init__(self, cfg: PoolConfig, nodes: NodeManager) -> None:
        self.cfg = cfg
        self.nodes = nodes
        self._last_prune = 0.0

    def inject_from_source(self, node_source_cfg) -> int:
        """从节点源注入新节点 (仅添加本地没有的)。

        返回新增节点数。节点源加载失败时静默降级 (返回 0)。
        """
        try:
            proxies = load_nodes(node_source_cfg)
        except Exception as exc:  # noqa: BLE001 - 节点源失败不应中断主循环
            logger.warning("节点源加载失败: %s", exc)
            return 0

        added = 0
        for p in proxies:
            node_id = p.get("name")
            if not node_id:
                continue
            if self.nodes.get(node_id) is not None:
                continue
            if len(self.nodes.list()) >= self.cfg.max_nodes:
                logger.warning("节点池已达上限 %d，跳过注入", self.cfg.max_nodes)
                break
            self.nodes.add(
                ProxyNode(
                    node_id=node_id,
                    server=p.get("server", ""),
                    port=int(p.get("port", 0) or 0),
                    protocol=str(p.get("type", "unknown")).lower(),
                    country=p.get("country", ""),
                )
            )
            added += 1
        if added:
            logger.info("节点源注入 %d 个新节点", added)
        return added

    def prune(self, probe, bandit=None) -> int:
        """修剪节点池: 淘汰 DEAD 节点 + 长期低分节点。

        返回移除的节点数。受 prune_interval_min 节流，避免每轮都扫描。
        """
        now = time.time()
        if now - self._last_prune < self.cfg.prune_interval_min * 60:
            return 0
        self._last_prune = now

        removed = 0
        for node in self.nodes.list():
            # 1. DEAD 节点直接移除
            if node.status == NodeStatus.DEAD:
                self.nodes.remove(node.node_id)
                if bandit:
                    bandit.forget(node.node_id)
                removed += 1
                continue
            # 2. 长期低成功率节点移除
            history = probe.history(
                node.node_id, window_sec=self.cfg.score_window_min * 60
            )
            if len(history) >= self.cfg.min_history:
                success_rate = sum(1 for r in history if r.success) / len(history)
                if success_rate < self.cfg.min_success_rate:
                    self.nodes.remove(node.node_id)
                    if bandit:
                        bandit.forget(node.node_id)
                    removed += 1
        if removed:
            logger.info("节点池修剪: 移除 %d 个节点", removed)
        return removed
