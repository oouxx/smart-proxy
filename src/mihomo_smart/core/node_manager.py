"""Node Manager: 代理节点生命周期管理。

节点状态机:
    NEW -> PROBING -> ACTIVE -> BAD -> DEAD
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


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

    def transition(self, new_status: NodeStatus) -> None:
        """状态迁移。"""
        self.status = new_status


class NodeManager:
    """管理节点池。"""

    def __init__(self) -> None:
        self._nodes: dict[str, ProxyNode] = {}

    def add(self, node: ProxyNode) -> None:
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
