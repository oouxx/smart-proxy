"""配置加载健壮性测试。"""
from __future__ import annotations

from mihomo_smart.config import Config


def test_load_tolerates_unknown_keys(tmp_path):
    """未知/拼错的 YAML key 应被忽略而非抛 TypeError。"""
    p = tmp_path / "config.yaml"
    p.write_text(
        """
probe:
  timout_ms: 9999        # 拼错 -> 应忽略
  unknown_field: 123
  interval_sec: 42       # 合法字段 -> 生效
model:
  extra: 1
"""
    )
    cfg = Config.load(p)
    assert cfg.probe.interval_sec == 42
    assert cfg.probe.timeout_ms == 5000  # 默认值保留，未被拼错字段覆盖


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nope.yaml")
    assert cfg.probe.timeout_ms == 5000
    assert cfg.model.model_weight == 0.7
