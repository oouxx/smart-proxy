"""Probe Engine 与 Go 探针的 HTTP 集成测试。"""
import asyncio
import socket

import pytest

from mihomo_smart.config import ProbeConfig
from mihomo_smart.core.node_manager import NodeManager, ProxyNode
from mihomo_smart.core.probe import ProbeEngine


def _free_port() -> int:
    """获取一个随机空闲端口, 避免与残留探针进程冲突。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_probe_engine_calls_go_probe():
    """验证 Python ProbeEngine 通过 HTTP 调用 Go 探针并返回结果。

    用 /probe_all (节点直接传入请求, 不依赖订阅文件)。目标指向本地立即关闭的
    TCP 服务, ss 隧道握手快速失败 -> success=False, 验证 HTTP 集成链路。
    """
    # 本地 TCP 服务, 接受连接后立即关闭 (非真实 ss 服务, 握手会快速失败)
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    cfg = ProbeConfig(engine_binary="bin/probe-engine", engine_addr=f"127.0.0.1:{_free_port()}")
    engine = ProbeEngine(cfg, NodeManager())
    await engine.start_engine()
    try:
        nodes = [
            {"name": "test-1", "type": "ss", "server": "127.0.0.1", "port": port,
             "cipher": "aes-128-gcm", "password": "x"},
        ]
        results = await engine.probe_all(nodes)
        assert len(results) == 1
        assert results[0].node_id == "test-1"
        # 非真实 ss 服务, 隧道握手失败 -> success=False (快速返回)
        assert results[0].success is False
    finally:
        await engine.stop_engine()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_probe_engine_handles_unreachable_node():
    """不可达节点应返回 success=False 而非抛异常。"""
    cfg = ProbeConfig(engine_binary="bin/probe-engine", engine_addr=f"127.0.0.1:{_free_port()}")
    nodes = NodeManager()
    # 使用保留地址，确保连接失败
    nodes.add(ProxyNode(node_id="dead-1", server="192.0.2.1", port=9, protocol="ss"))

    engine = ProbeEngine(cfg, nodes)
    await engine.start_engine()
    try:
        node = nodes.get("dead-1")
        assert node is not None
        result = await engine._probe_node(node)
        assert result.node_id == "dead-1"
        assert result.success is False
    finally:
        await engine.stop_engine()


@pytest.mark.asyncio
async def test_start_engine_reuses_existing():
    """端口已有探针在服务时，start_engine 应复用而非重复启动。"""
    port = _free_port()
    cfg = ProbeConfig(engine_binary="bin/probe-engine", engine_addr=f"127.0.0.1:{port}")
    engine1 = ProbeEngine(cfg, NodeManager())
    await engine1.start_engine()
    try:
        engine2 = ProbeEngine(cfg, NodeManager())
        await engine2.start_engine()
        # 复用已有探针：engine2 不拥有进程
        assert engine2._owns_proc is False
        # 停止 engine2 不应杀掉 engine1 的探针
        await engine2.stop_engine()
        assert await engine1._ping() is True
    finally:
        await engine1.stop_engine()
