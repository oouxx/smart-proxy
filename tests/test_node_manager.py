"""基础测试。"""
from subs_check.core.node_manager import NodeManager, NodeStatus, ProxyNode


def test_node_lifecycle():
    mgr = NodeManager()
    node = ProxyNode(node_id="jp-1", server="1.2.3.4", port=443, protocol="vmess")
    mgr.add(node)
    assert mgr.get("jp-1") is node
    assert node.status == NodeStatus.NEW

    mgr.set_status("jp-1", NodeStatus.ACTIVE)
    assert node.status == NodeStatus.ACTIVE
    assert mgr.list(NodeStatus.ACTIVE) == [node]

    mgr.remove("jp-1")
    assert mgr.get("jp-1") is None
