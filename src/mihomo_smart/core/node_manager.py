"""Node Manager: 代理节点生命周期管理。

节点状态机:
    NEW -> PROBING -> ACTIVE -> BAD -> DEAD
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

logger = logging.getLogger(__name__)


class NodeStatus(str, Enum):
    NEW = "NEW"
    PROBING = "PROBING"
    ACTIVE = "ACTIVE"
    BAD = "BAD"
    DEAD = "DEAD"


@dataclass
class ProxyNode:
    """代理节点。"""

    node_id: str
    server: str
    port: int
    protocol: str
    country: str = ""
    isp: str = ""
    status: NodeStatus = NodeStatus.NEW
    created_time: float = field(default_factory=time.time)
    tags: set[str] = field(default_factory=set)

    # 合法状态迁移表 (迁移到集合外的状态视为非法)
    _VALID_TRANSITIONS: ClassVar[dict[NodeStatus, set[NodeStatus]]] = {
        NodeStatus.NEW: {NodeStatus.PROBING},
        NodeStatus.PROBING: {NodeStatus.ACTIVE, NodeStatus.BAD, NodeStatus.DEAD},
        NodeStatus.ACTIVE: {NodeStatus.BAD, NodeStatus.DEAD},
        NodeStatus.BAD: {NodeStatus.PROBING, NodeStatus.DEAD},
        NodeStatus.DEAD: set(),
    }

    def transition(self, new_status: NodeStatus) -> None:
        """状态迁移，非法迁移抛出 ValueError。"""
        valid = self._VALID_TRANSITIONS.get(self.status, set())
        if new_status not in valid:
            raise ValueError(f"非法状态迁移: {self.status.value} -> {new_status.value}")
        self.status = new_status


class NodeManager:
    """管理节点池。"""

    def __init__(self) -> None:
        self._nodes: dict[str, ProxyNode] = {}

    def add(self, node: ProxyNode) -> None:
        if node.node_id in self._nodes:
            logger.info("节点 %s 已存在，覆盖", node.node_id)
        self._nodes[node.node_id] = node

    def remove(self, node_id: str) -> None:
        self._nodes.pop(node_id, None)

    def get(self, node_id: str) -> ProxyNode | None:
        return self._nodes.get(node_id)

    def list(self, status: NodeStatus | None = None) -> list[ProxyNode]:
        nodes = list(self._nodes.values())
        if status is not None:
            nodes = [n for n in nodes if n.status == status]
        return nodes

    def set_status(self, node_id: str, status: NodeStatus) -> None:
        node = self._nodes.get(node_id)
        if node:
            node.transition(status)

    def add_tag(self, node_id: str, tag: str) -> None:
        node = self._nodes.get(node_id)
        if node:
            node.tags.add(tag)
