"""核心链路测试: 状态机迁移 + 评分融合。"""
from __future__ import annotations

from mihomo_smart.cli import _apply_node_status
from mihomo_smart.config import ModelConfig
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
