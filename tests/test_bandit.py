"""Bandit 在线学习测试。"""
from __future__ import annotations

from mihomo_smart.ml.bandit import BanditLearner


def test_select_unexplored_arms_first():
    """未探索的臂应优先被选择 (鼓励探索)。"""
    b = BanditLearner(seed=42)
    chosen = b.select(["a", "b", "c"], top_k=1)
    assert chosen[0] in {"a", "b", "c"}


def test_select_top_k():
    """应返回 top_k 个未探索臂。"""
    b = BanditLearner(seed=42)
    chosen = b.select(["a", "b", "c"], top_k=2)
    assert len(chosen) == 2
    assert set(chosen) <= {"a", "b", "c"}


def test_ucb_prefers_high_value_arm():
    """利用阶段应偏向累计奖励高的臂。"""
    b = BanditLearner(exploration_rate=0.0, seed=42)
    # 先给 a 高奖励、b 低奖励，各选多次
    for _ in range(10):
        b.update("a", 0.9)
        b.update("b", 0.1)
    chosen = b.select(["a", "b"], top_k=1)
    assert chosen[0] == "a"


def test_update_incremental_mean():
    """update 应维护增量均值。"""
    b = BanditLearner()
    b.update("a", 0.0)
    b.update("a", 1.0)
    assert abs(b._values["a"] - 0.5) < 1e-9
    assert b._counts["a"] == 2


def test_update_clamps_reward():
    """奖励应被限制在 0~1。"""
    b = BanditLearner()
    b.update("a", 5.0)
    b.update("b", -3.0)
    assert b._values["a"] == 1.0
    assert b._values["b"] == 0.0


def test_forget_removes_arm():
    """forget 应移除臂的统计。"""
    b = BanditLearner()
    b.update("a", 0.5)
    b.forget("a")
    assert "a" not in b._counts
    assert "a" not in b._values


def test_save_load_roundtrip(tmp_path):
    """save/load 应完整还原学习状态。"""
    b = BanditLearner(alpha=2.0, exploration_rate=0.3, seed=1)
    b.update("a", 0.8)
    b.update("a", 0.6)
    b.update("b", 0.2)
    path = tmp_path / "bandit.json"
    b.save(path)

    b2 = BanditLearner()
    b2.load(path)
    assert b2.alpha == 2.0
    assert b2.exploration_rate == 0.3
    assert b2._counts == {"a": 2, "b": 1}
    assert abs(b2._values["a"] - 0.7) < 1e-9
    assert abs(b2._values["b"] - 0.2) < 1e-9


def test_load_missing_file(tmp_path):
    """加载不存在的文件应从零开始，不报错。"""
    b = BanditLearner()
    b.load(tmp_path / "nope.json")
    assert b._counts == {}
