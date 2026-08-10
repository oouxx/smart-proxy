"""Probe Engine 与 Go 探针的 HTTP 集成测试。"""
import asyncio

import pytest

from mihomo_smart.config import ProbeConfig
from mihomo_smart.core.node_manager import NodeManager, ProxyNode
from mihomo_smart.core.probe import ProbeEngine


@pytest.mark.asyncio
async def test_probe_engine_calls_go_probe():
    """启动 Go 探针子进程，验证 HTTP 调用返回真实探测结果。"""
    # 本地起一个 TCP 服务作为探测目标，保证测试确定且不依赖外网
    # handler 需关闭连接，否则 server.wait_closed() 会永远等待
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    cfg = ProbeConfig(engine_binary="bin/probe-engine", engine_addr="127.0.0.1:9101")
    nodes = NodeManager()
    nodes.add(ProxyNode(node_id="test-1", server="127.0.0.1", port=port, protocol="ss"))

    engine = ProbeEngine(cfg, nodes)
    await engine.start_engine()
    try:
        result = await engine._probe_node(nodes.get("test-1"))
        # 本地 TCP 应能建立连接
        assert result.node_id == "test-1"
        assert result.tcp_connect_ms is not None
        assert result.success is True
    finally:
        await engine.stop_engine()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_probe_engine_handles_unreachable_node():
    """不可达节点应返回 success=False 而非抛异常。"""
    cfg = ProbeConfig(engine_binary="bin/probe-engine", engine_addr="127.0.0.1:9102")
    nodes = NodeManager()
    # 使用保留地址，确保连接失败
    nodes.add(ProxyNode(node_id="dead-1", server="192.0.2.1", port=9, protocol="ss"))

    engine = ProbeEngine(cfg, nodes)
    await engine.start_engine()
    try:
        result = await engine._probe_node(nodes.get("dead-1"))
        assert result.node_id == "dead-1"
        assert result.success is False
    finally:
        await engine.stop_engine()


@pytest.mark.asyncio
async def test_start_engine_reuses_existing():
    """端口已有探针在服务时，start_engine 应复用而非重复启动。"""
    cfg = ProbeConfig(engine_binary="bin/probe-engine", engine_addr="127.0.0.1:9103")
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
