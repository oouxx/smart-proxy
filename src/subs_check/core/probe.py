"""Probe Engine: 主动探测节点质量。

探测指标:
- 网络: latency, tcp_connect_time, tls_handshake_time, packet_loss, jitter
- 可用性: success_rate, timeout_count, disconnect_count
- 性能: download_speed, upload_speed, first_byte_time

实际探测由 Go 侧 (mihomo/探针) 完成，这里负责调度与指标收集。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from ..config import ProbeConfig
from .node_manager import NodeManager, NodeStatus

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    """单次探测结果。"""

    node_id: str
    timestamp: float = field(default_factory=time.time)
    latency_ms: float | None = None
    tcp_connect_ms: float | None = None
    tls_handshake_ms: float | None = None
    packet_loss: float | None = None
    jitter_ms: float | None = None
    success: bool = False
    download_speed_kbps: float | None = None
    first_byte_ms: float | None = None


class ProbeEngine:
    """调度探测任务并收集指标。"""

    def __init__(self, cfg: ProbeConfig, nodes: NodeManager) -> None:
        self.cfg = cfg
        self.nodes = nodes
        self._results: dict[str, list[ProbeResult]] = {}
        self._running = False

    async def run(self) -> None:
        """周期探测循环。"""
        self._running = True
        while self._running:
            await self._probe_once()
            await asyncio.sleep(self.cfg.interval_sec)

    async def stop(self) -> None:
        self._running = False

    async def _probe_once(self) -> None:
        """对活跃节点执行一轮探测。"""
        targets = self.nodes.list(NodeStatus.ACTIVE) + self.nodes.list(NodeStatus.PROBING)
        if not targets:
            return
        sem = asyncio.Semaphore(self.cfg.concurrency)

        async def _probe(node_id: str) -> None:
            async with sem:
                result = await self._probe_node(node_id)
                self._results.setdefault(node_id, []).append(result)

        await asyncio.gather(*(_probe(n.node_id) for n in targets))

    async def _probe_node(self, node_id: str) -> ProbeResult:
        """单节点探测。实际网络探测委托给 Go 探针，这里为占位实现。"""
        # TODO: 调用 Go 探针二进制获取真实指标
        return ProbeResult(node_id=node_id, success=True, latency_ms=100.0)

    def history(self, node_id: str, window_sec: int | None = None) -> list[ProbeResult]:
        """获取节点历史探测结果。"""
        results = self._results.get(node_id, [])
        if window_sec is not None:
            cutoff = time.time() - window_sec
            results = [r for r in results if r.timestamp >= cutoff]
        return results
