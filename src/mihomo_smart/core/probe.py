"""Probe Engine: 主动探测节点质量。

探测指标:
- 网络: latency, tcp_connect_time, tls_handshake_time, packet_loss, jitter
- 可用性: success_rate, timeout_count, disconnect_count
- 性能: download_speed, upload_speed, first_byte_time

实际网络探测由 Go 侧 (bin/probe-engine) 完成，这里负责:
- 启动/停止 Go 探针子进程 (若端口已有探针则复用，避免孤儿进程堆积)
- 通过 HTTP 调用探针的 /probe 接口
- 调度探测任务并收集指标
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass, field

import httpx

from ..config import ProbeConfig
from .node_manager import NodeManager, NodeStatus, ProxyNode

logger = logging.getLogger(__name__)

# 常见走 TLS 的协议
_TLS_PROTOCOLS = {"vmess", "vless", "trojan", "hysteria2", "hysteria"}


@dataclass
class ProbeResult:
    """单次探测结果。"""

    node_id: str
    timestamp: float = field(default_factory=time.time)
    latency_ms: float | None = None
    latency_min_ms: float | None = None
    latency_max_ms: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    tcp_connect_ms: float | None = None
    tls_handshake_ms: float | None = None
    packet_loss: float | None = None
    jitter_ms: float | None = None
    success_rate: float | None = None
    success: bool = False
    download_speed_kbps: float | None = None
    upload_speed_kbps: float | None = None
    first_byte_ms: float | None = None


class ProbeEngine:
    """调度探测任务并收集指标。

    探测由 Go 探针 (bin/probe-engine) 完成，内嵌 mihomo 库走真实代理隧道
    测量延迟/抖动/丢包/速度等指标。这里负责:
    - 启动/停止 Go 探针子进程 (若端口已有探针则复用，避免孤儿进程堆积)
    - 通过 HTTP 调用探针的 /probe 接口
    - 调度探测任务并收集指标
    """

    def __init__(self, cfg: ProbeConfig, nodes: NodeManager) -> None:
        self.cfg = cfg
        self.nodes = nodes
        self._results: dict[str, list[ProbeResult]] = {}
        self._running = False
        self._stop_event = asyncio.Event()
        self._proc: subprocess.Popen | None = None
        self._owns_proc = False
        self._client: httpx.AsyncClient | None = None
        self._base_url = f"http://{cfg.engine_addr}"

    # ---------- Go 探针子进程管理 ----------

    async def start_engine(self) -> None:
        """启动 Go 探针子进程；若端口已有探针在服务则直接复用。

        复用已有探针可避免重复进程堆积（例如上次运行残留的孤儿进程）。
        """
        if self._proc and self._proc.poll() is None:
            return
        # 端口已有探针在服务 → 复用，不重复启动
        if await self._ping():
            logger.info("复用已有探针引擎: %s", self.cfg.engine_addr)
            self._proc = None
            self._owns_proc = False
            return
        logger.info("启动探针引擎: %s", self.cfg.engine_binary)
        self._proc = await asyncio.to_thread(
            subprocess.Popen,
            [
                self.cfg.engine_binary,
                "-addr",
                self.cfg.engine_addr,
                "-config",
                self.cfg.engine_config,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._owns_proc = True
        for _ in range(50):
            if await self._ping():
                logger.info("探针引擎就绪: %s", self.cfg.engine_addr)
                return
            await asyncio.sleep(0.2)
        # 启动失败：清理子进程后抛错
        await self.stop_engine()
        raise RuntimeError("探针引擎未在预期时间内就绪")

    async def stop_engine(self) -> None:
        """停止 Go 探针子进程（仅停止本实例启动的进程，不影响复用的进程）。"""
        if self._owns_proc and self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(self._proc.wait), 5)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None
        self._owns_proc = False
        await self.close()

    async def close(self) -> None:
        """关闭共享 HTTP 客户端。"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取共享 HTTP 客户端（懒创建，避免每次探测新建连接）。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.cfg.timeout_ms / 1000 + 5,
                trust_env=False,
            )
        return self._client

    async def _ping(self) -> bool:
        try:
            client = await self._get_client()
            r = await client.get(f"{self._base_url}/health", timeout=1.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    # ---------- 探测调度 ----------

    async def run(self, on_round=None) -> None:
        """周期探测循环。

        on_round: 每完成一轮探测后的异步回调 (可选的
                  callable)，用于驱动后续的评分/选择等编排逻辑。
        """
        self._running = True
        self._stop_event.clear()
        while self._running:
            await self.probe_once()
            if on_round is not None:
                await on_round()
            # 用事件等待替代固定 sleep，stop() 可立即中断
            # 有 NEW/PROBING 节点在探测时用快速间隔，否则用常规间隔
            interval = self._wait_interval()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=interval
                )
            except asyncio.TimeoutError:
                pass

    def _wait_interval(self) -> int:
        """存在待探测/探测中的新节点时返回快速间隔，否则返回常规间隔。"""
        if self.nodes.list(NodeStatus.NEW) or self.nodes.list(NodeStatus.PROBING):
            return self.cfg.fast_interval_sec
        return self.cfg.interval_sec

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()

    async def probe_once(self) -> None:
        """对候选节点执行一轮探测 (通过 Go 探针隧道)。

        覆盖 NEW/PROBING/ACTIVE: NEW 首次被探测后由状态机转为 PROBING，
        BAD/DEAD 是降级/淘汰节点，不参与常规探测。
        """
        targets = (
            self.nodes.list(NodeStatus.NEW)
            + self.nodes.list(NodeStatus.PROBING)
            + self.nodes.list(NodeStatus.ACTIVE)
        )
        if not targets:
            return
        sem = asyncio.Semaphore(self.cfg.concurrency)

        async def _probe(node: ProxyNode) -> None:
            async with sem:
                result = await self._probe_node(node)
                self._results.setdefault(node.node_id, []).append(result)

        await asyncio.gather(*(_probe(n) for n in targets))

    async def _probe_node(self, node: ProxyNode) -> ProbeResult:
        """对单个节点执行探测，通过 HTTP 调用 Go 探针。"""
        payload = {
            "node_id": node.node_id,
            "server": node.server,
            "port": node.port,
            "tls": node.protocol.lower() in _TLS_PROTOCOLS,
            "probe_url": self.cfg.probe_url,
            "download_url": self.cfg.download_url,
            "upload_url": self.cfg.upload_url,
            "timeout_ms": self.cfg.timeout_ms,
            "latency_samples": self.cfg.latency_samples,
        }
        try:
            client = await self._get_client()
            r = await client.post(f"{self._base_url}/probe", json=payload)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as exc:
            logger.warning("探测节点 %s 失败: %s", node.node_id, exc)
            return ProbeResult(node_id=node.node_id, success=False)

        return self._parse_result(data, node.node_id)

    async def probe_all(self, nodes: list[dict]) -> list[ProbeResult]:
        """批量探测整个节点池，返回与输入顺序一致的结果列表。

        nodes: 完整节点配置列表 (proxies 数组)。
        """
        payload = {
            "nodes": nodes,
            "probe_url": self.cfg.probe_url,
            "download_url": self.cfg.download_url,
            "upload_url": self.cfg.upload_url,
            "timeout_ms": self.cfg.timeout_ms,
            "latency_samples": self.cfg.latency_samples,
            "concurrency": self.cfg.concurrency,
        }
        # 批量探测耗时 = 节点数/并发 × 单节点超时，加 2 倍余量 + 60s 缓冲
        # (每批含下载测速约 3s 等开销，余量不足会导致 HTTP 超时)
        # httpx 0.28 的 Timeout 无 total 字段；read 超时会在等待服务器响应
        # (含批量计算) 期间持续生效，因此把 read 设为批量估算上限作为整体
        # 等待的近似截止，connect/write/pool 用单节点量级即可 (localhost)。
        batches = max(1, len(nodes) / max(self.cfg.concurrency, 1))
        total = self.cfg.timeout_ms / 1000 * batches * 2 + 60
        base = self.cfg.timeout_ms / 1000 + 5
        timeout = httpx.Timeout(
            connect=base,
            read=total,
            write=base,
            pool=base,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                r = await client.post(f"{self._base_url}/probe_all", json=payload)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as exc:
            logger.error("批量探测失败: %s", exc)
            raise
        return [
            self._parse_result(item, item.get("node_id", ""))
            for item in data.get("results", [])
        ]

    def _parse_result(self, data: dict, default_node_id: str) -> ProbeResult:
        """把 Go 探针返回的 JSON 解析为 ProbeResult。"""
        return ProbeResult(
            node_id=data.get("node_id", default_node_id),
            timestamp=float(data.get("timestamp", time.time())),
            latency_ms=data.get("latency_ms"),
            latency_min_ms=data.get("latency_min_ms"),
            latency_max_ms=data.get("latency_max_ms"),
            latency_p50_ms=data.get("latency_p50_ms"),
            latency_p95_ms=data.get("latency_p95_ms"),
            tcp_connect_ms=data.get("tcp_connect_ms"),
            tls_handshake_ms=data.get("tls_handshake_ms"),
            packet_loss=data.get("packet_loss"),
            jitter_ms=data.get("jitter_ms"),
            success_rate=data.get("success_rate"),
            success=bool(data.get("success", False)),
            download_speed_kbps=data.get("download_speed_kbps"),
            upload_speed_kbps=data.get("upload_speed_kbps"),
            first_byte_ms=data.get("first_byte_ms"),
        )

    def history(self, node_id: str, window_sec: int | None = None) -> list[ProbeResult]:
        """获取节点历史探测结果。"""
        results = self._results.get(node_id, [])
        if window_sec is not None:
            cutoff = time.time() - window_sec
            results = [r for r in results if r.timestamp >= cutoff]
        return results
