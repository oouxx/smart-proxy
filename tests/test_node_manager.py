"""基础测试。"""
import pytest

from mihomo_smart.core.node_manager import NodeManager, NodeStatus, ProxyNode


def test_node_lifecycle():
    mgr = NodeManager()
    node = ProxyNode(node_id="jp-1", server="1.2.3.4", port=443, protocol="vmess")
    mgr.add(node)
    assert mgr.get("jp-1") is node
    assert node.status == NodeStatus.NEW

    # 合法迁移链: NEW -> PROBING -> ACTIVE
    mgr.set_status("jp-1", NodeStatus.PROBING)
    assert node.status == NodeStatus.PROBING
    mgr.set_status("jp-1", NodeStatus.ACTIVE)
    assert node.status == NodeStatus.ACTIVE
    assert mgr.list(NodeStatus.ACTIVE) == [node]

    mgr.remove("jp-1")
    assert mgr.get("jp-1") is None


def test_illegal_transition_raises():
    """非法状态迁移应抛 ValueError (如 NEW 直接到 ACTIVE)。"""
    node = ProxyNode(node_id="n", server="1.1.1.1", port=443, protocol="ss")
    assert node.status == NodeStatus.NEW
    with pytest.raises(ValueError):
        node.transition(NodeStatus.ACTIVE)


def test_add_overwrite_keeps_latest():
    """add 重复 node_id 时应覆盖为最新节点。"""
    mgr = NodeManager()
    mgr.add(ProxyNode(node_id="a", server="1.1.1.1", port=443, protocol="ss"))
    newer = ProxyNode(node_id="a", server="2.2.2.2", port=443, protocol="trojan")
    mgr.add(newer)
    assert mgr.get("a") is newer
