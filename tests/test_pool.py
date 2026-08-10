"""动态节点池测试。"""
from __future__ import annotations

from mihomo_smart.config import PoolConfig
from mihomo_smart.core.node_manager import NodeManager, NodeStatus, ProxyNode
from mihomo_smart.core.pool import DynamicPool
from mihomo_smart.core.probe import ProbeEngine, ProbeResult


def _node(mgr: NodeManager, node_id: str, status: NodeStatus = NodeStatus.ACTIVE) -> ProxyNode:
    node = ProxyNode(node_id=node_id, server="127.0.0.1", port=443, protocol="vmess", status=status)
    mgr.add(node)
    return node


def test_inject_from_source_adds_new_nodes(tmp_path):
    """注入应添加本地没有的节点，跳过已存在的。"""
    src = tmp_path / "sub.yaml"
    src.write_text(
        "proxies:\n"
        "  - {name: jp-1, server: 1.1.1.1, port: 443, type: vmess}\n"
        "  - {name: us-1, server: 2.2.2.2, port: 443, type: trojan}\n"
    )
    from mihomo_smart.config import NodeSourceConfig

    mgr = NodeManager()
    _node(mgr, "jp-1")  # 已存在
    pool = DynamicPool(PoolConfig(), mgr)

    added = pool.inject_from_source(NodeSourceConfig(path=str(src)))
    assert added == 1
    us1 = mgr.get("us-1")
    assert us1 is not None
    assert us1.protocol == "trojan"
    assert us1.server == "2.2.2.2"


def test_inject_missing_source_returns_zero(tmp_path):
    """节点源文件不存在时应静默降级，返回 0。"""
    mgr = NodeManager()
    pool = DynamicPool(PoolConfig(), mgr)
    from mihomo_smart.config import NodeSourceConfig

    added = pool.inject_from_source(NodeSourceConfig(path=str(tmp_path / "nope.yaml")))
    assert added == 0


def test_prune_removes_dead_nodes():
    """修剪应移除 DEAD 节点。"""
    mgr = NodeManager()
    _node(mgr, "dead-1", NodeStatus.DEAD)
    _node(mgr, "alive-1", NodeStatus.ACTIVE)
    pool = DynamicPool(PoolConfig(prune_interval_min=0), mgr)
    probe = ProbeEngine.__new__(ProbeEngine)
    probe._results = {}

    removed = pool.prune(probe)
    assert removed == 1
    assert mgr.get("dead-1") is None
    assert mgr.get("alive-1") is not None


def test_prune_removes_low_success_nodes():
    """长期低成功率的节点应被淘汰。"""
    mgr = NodeManager()
    _node(mgr, "bad-1")
    _node(mgr, "good-1")
    pool = DynamicPool(PoolConfig(prune_interval_min=0, min_history=3, min_success_rate=0.5), mgr)
    probe = ProbeEngine.__new__(ProbeEngine)
    probe._results = {
        "bad-1": [
            ProbeResult(node_id="bad-1", success=False),
            ProbeResult(node_id="bad-1", success=False),
            ProbeResult(node_id="bad-1", success=False),
        ],
        "good-1": [
            ProbeResult(node_id="good-1", success=True),
            ProbeResult(node_id="good-1", success=True),
            ProbeResult(node_id="good-1", success=True),
        ],
    }

    removed = pool.prune(probe)
    assert removed == 1
    assert mgr.get("bad-1") is None
    assert mgr.get("good-1") is not None


def test_prune_throttled_by_interval():
    """修剪应受间隔节流，间隔内不重复执行。"""
    mgr = NodeManager()
    _node(mgr, "dead-1", NodeStatus.DEAD)
    pool = DynamicPool(PoolConfig(prune_interval_min=60), mgr)  # 默认 60 分钟
    probe = ProbeEngine.__new__(ProbeEngine)
    probe._results = {}

    # 首次调用应执行修剪
    removed = pool.prune(probe)
    assert removed == 1
    assert mgr.get("dead-1") is None

    # 间隔内再次调用应被节流 (无节点可移除)
    _node(mgr, "dead-2", NodeStatus.DEAD)
    removed = pool.prune(probe)
    assert removed == 0
    assert mgr.get("dead-2") is not None
