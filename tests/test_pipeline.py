"""核心链路测试: 状态机迁移 + 评分融合 + 选择器。"""
from __future__ import annotations

import pytest

from mihomo_smart.cli import _apply_node_status
from mihomo_smart.config import ModelConfig, SelectorConfig
from mihomo_smart.core.node_manager import NodeManager, NodeStatus, ProxyNode
from mihomo_smart.core.probe import ProbeEngine, ProbeResult
from mihomo_smart.ml.features import FeatureEngine
from mihomo_smart.ml.model import IntelligenceEngine, NodeRanker, RealtimeScorer


def _node(mgr: NodeManager, node_id: str, status: NodeStatus) -> ProxyNode:
    node = ProxyNode(node_id=node_id, server="127.0.0.1", port=443, protocol="vmess", status=status)
    mgr.add(node)
    return node


def test_apply_node_status_transitions():
    """验证状态机: NEW->PROBING, BAD 成功->PROBING (分阶段恢复)。"""
    mgr = NodeManager()
    _node(mgr, "new-1", NodeStatus.NEW)
    _node(mgr, "bad-1", NodeStatus.BAD)

    probe = ProbeEngine.__new__(ProbeEngine)  # 不实例化，只注入历史
    probe._results = {
        "new-1": [ProbeResult(node_id="new-1", success=True)],
        "bad-1": [
            ProbeResult(node_id="bad-1", success=False),
            ProbeResult(node_id="bad-1", success=False),
            ProbeResult(node_id="bad-1", success=True),
        ],
    }

    _apply_node_status(mgr, probe)

    new1 = mgr.get("new-1")
    bad1 = mgr.get("bad-1")
    assert new1 is not None and bad1 is not None
    assert new1.status == NodeStatus.PROBING  # NEW 首次探测后进入 PROBING
    assert bad1.status == NodeStatus.PROBING  # 分阶段恢复: BAD 成功先进入试探


def test_apply_node_status_probing_recovers_to_active():
    """PROBING 连续成功 3 次后回 ACTIVE (分阶段恢复)。"""
    mgr = NodeManager()
    _node(mgr, "rec-1", NodeStatus.PROBING)
    probe = ProbeEngine.__new__(ProbeEngine)
    probe._results = {
        "rec-1": [
            ProbeResult(node_id="rec-1", success=True) for _ in range(3)
        ]
    }
    _apply_node_status(mgr, probe)
    rec1 = mgr.get("rec-1")
    assert rec1 is not None
    assert rec1.status == NodeStatus.ACTIVE


def test_apply_node_status_active_to_bad():
    """连续 3 次失败: ACTIVE -> BAD。"""
    mgr = NodeManager()
    _node(mgr, "act-1", NodeStatus.ACTIVE)
    probe = ProbeEngine.__new__(ProbeEngine)
    probe._results = {
        "act-1": [
            ProbeResult(node_id="act-1", success=False),
            ProbeResult(node_id="act-1", success=False),
            ProbeResult(node_id="act-1", success=False),
        ]
    }
    _apply_node_status(mgr, probe)
    act1 = mgr.get("act-1")
    assert act1 is not None
    assert act1.status == NodeStatus.BAD


def test_apply_node_status_bad_to_dead():
    """连续 10 次失败: BAD -> DEAD (供动态池淘汰)。"""
    mgr = NodeManager()
    _node(mgr, "dead-1", NodeStatus.BAD)
    probe = ProbeEngine.__new__(ProbeEngine)
    probe._results = {
        "dead-1": [
            ProbeResult(node_id="dead-1", success=False) for _ in range(10)
        ]
    }
    _apply_node_status(mgr, probe)
    dead1 = mgr.get("dead-1")
    assert dead1 is not None
    assert dead1.status == NodeStatus.DEAD


def test_score_node_combines_model_and_realtime():
    """score_node 应产出 0~1 的分值，且无模型时仍有实时分兜底。"""
    cfg = ModelConfig(model_path="models/does_not_exist.lgb")  # 强制走实时兜底
    features = FeatureEngine()
    ranker = NodeRanker(cfg, features)
    ranker.load()  # 模型不存在 -> _model = None

    intelligence = IntelligenceEngine(cfg, ranker, RealtimeScorer())
    node = ProxyNode(node_id="n1", server="1.1.1.1", port=443, protocol="vmess")
    history = [
        ProbeResult(node_id="n1", latency_ms=80.0, success=True),
        ProbeResult(node_id="n1", latency_ms=120.0, success=True),
    ]

    score = intelligence.score_node(node, history)
    assert 0.0 <= score <= 1.0
    # 实时分(延迟低+成功率100%)应 > 0
    assert score > 0.0


def test_selector_top_n_filter():
    """选择器应按评分取 Top N 并过滤低于 min_score 的节点。"""
    cfg = SelectorConfig(top_n=2, min_score=0.5, group="GLOBAL")
    from mihomo_smart.core.mihomo import MihomoController
    from mihomo_smart.core.selector import SelectorService

    nodes = NodeManager()
    selector = SelectorService(cfg, nodes, MihomoController.__new__(MihomoController))

    scores = {"a": 0.9, "b": 0.7, "c": 0.2, "d": 0.6}
    top = selector.select_top(scores)
    assert top == ["a", "b"]  # 0.9, 0.7 达标且 top_n=2; c 不达标


class _FakeMihomoNodes:
    """伪造 mihomo，模拟 get_nodes 返回的节点列表。"""

    def __init__(self, nodes: list[dict]) -> None:
        self._nodes = nodes

    async def get_nodes(self):
        return self._nodes


@pytest.mark.asyncio
async def test_sync_from_mihomo():
    """NodeManager 应从 mihomo 内核同步节点成员: 新增/更新/删除。"""
    mgr = NodeManager()
    # 本地有一个内核中不存在的旧节点
    mgr.add(ProxyNode(node_id="stale", server="", port=0, protocol="vmess"))

    fake = _FakeMihomoNodes(
        [
            {"node_id": "jp-1", "protocol": "vmess"},
            {"node_id": "us-1", "protocol": "trojan"},
        ]
    )
    await mgr.sync_from_mihomo(fake)

    # 新增的两个节点
    jp1 = mgr.get("jp-1")
    us1 = mgr.get("us-1")
    assert jp1 is not None
    assert us1 is not None
    assert us1.protocol == "trojan"
    # 旧的 stale 节点被移除
    assert mgr.get("stale") is None

    # 再次同步: 更新协议 + 移除已消失的节点
    fake2 = _FakeMihomoNodes([{"node_id": "jp-1", "protocol": "hysteria2"}])
    await mgr.sync_from_mihomo(fake2)
    jp1 = mgr.get("jp-1")
    assert jp1 is not None
    assert jp1.protocol == "hysteria2"
    assert mgr.get("us-1") is None
