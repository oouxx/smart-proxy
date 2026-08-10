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

    async def sync_from_mihomo(self, mihomo) -> None:
        """从 mihomo 内核同步节点列表。

        mihomo 是节点成员的唯一数据源 (读取订阅/proxy-providers)。
        这里做增量同步:
        - 新增: 内核有而本地没有的节点 -> NEW
        - 更新: 协议类型变化
        - 删除: 内核已移除的节点
        """
        remote = await mihomo.get_nodes()
        seen: set[str] = set()
        for info in remote:
            node_id = info["node_id"]
            protocol = info.get("protocol", "unknown")
            seen.add(node_id)
            existing = self._nodes.get(node_id)
            if existing is None:
                # 新增节点: server/port 由内核持有，Python 侧仅用于调度状态
                self._nodes[node_id] = ProxyNode(
                    node_id=node_id,
                    server="",
                    port=0,
                    protocol=protocol,
                )
            elif existing.protocol != protocol:
                existing.protocol = protocol
        # 移除内核中已不存在的节点
        for node_id in list(self._nodes):
            if node_id not in seen:
                self._nodes.pop(node_id, None)
