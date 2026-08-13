"""FastAPI 服务集成测试: serve 探针循环共享的节点状态/评分暴露。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from mihomo_smart.api import create_app
from mihomo_smart.config import Config
from mihomo_smart.core.node_manager import NodeManager, NodeStatus, ProxyNode


def _app_with_state() -> TestClient:
    mgr = NodeManager()
    mgr.add(
        ProxyNode(
            node_id="jp-1", server="1.1.1.1", port=443, protocol="vmess",
            country="JP", status=NodeStatus.ACTIVE,
        )
    )
    mgr.add(
        ProxyNode(
            node_id="us-1", server="2.2.2.2", port=443, protocol="trojan",
            status=NodeStatus.NEW,
        )
    )
    scores = {"jp-1": 0.87, "us-1": 0.5}
    app = create_app(Config(), node_manager=mgr, scores=scores)
    return TestClient(app)


def test_health():
    assert TestClient(create_app(Config())).get("/health").json() == {"status": "ok"}


def test_proxies_exposes_loop_state():
    """serve 探针循环维护的节点状态/评分应暴露到 /proxies。"""
    client = _app_with_state()
    r = client.get("/proxies")
    assert r.status_code == 200
    proxies = r.json()["proxies"]
    assert len(proxies) == 2
    by_id = {p["node_id"]: p for p in proxies}
    assert by_id["jp-1"]["status"] == "ACTIVE"
    assert by_id["jp-1"]["score"] == 0.87


def test_scores_returns_realtime_scores():
    client = _app_with_state()
    assert client.get("/scores").json() == {"scores": {"jp-1": 0.87, "us-1": 0.5}}


def test_empty_state_backward_compatible():
    """未传入 node_manager/scores 时返回空，保持向后兼容。"""
    client = TestClient(create_app(Config()))
    assert client.get("/proxies").json() == {"proxies": []}
    assert client.get("/scores").json() == {"scores": {}}


def test_download_smart_exists(tmp_path):
    """output 文件存在时应能下载 (自包含, 不依赖仓库里的 config/)。"""
    out = tmp_path / "mihomo-smart.yaml"
    out.write_text("proxies: []\n")
    cfg = Config()
    cfg.node_source.output = str(out)
    client = TestClient(create_app(cfg))
    assert client.get("/download/smart").status_code == 200


def test_download_missing_returns_404():
    """不存在的模型文件应返回 404。"""
    cfg = Config()
    cfg.model.model_path = "models/does_not_exist.lgb"
    client = TestClient(create_app(cfg))
    assert client.get("/download/model").status_code == 404
