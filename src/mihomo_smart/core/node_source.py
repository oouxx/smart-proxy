"""节点源管理: 从本地文件或远程 URL 加载节点池，并输出排序后的 mihomo-smart.yaml。

节点源是完整节点配置列表 (proxies 数组，含密码/uuid 等凭据)。
输出时仅重排序 + 截取 topN，节点配置原样保留。
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
import yaml

from ..config import NodeSourceConfig

logger = logging.getLogger(__name__)


def load_nodes(cfg: NodeSourceConfig) -> list[dict]:
    """从节点源加载完整节点配置列表 (proxies 数组)。

    优先使用远程 URL，否则使用本地文件。
    """
    if cfg.url:
        data = _fetch(cfg.url)
    else:
        path = Path(cfg.path)
        if not path.exists():
            raise FileNotFoundError(f"节点源文件不存在: {path}")
        data = path.read_text()

    doc = yaml.safe_load(data) or {}
    proxies = doc.get("proxies", [])
    if not isinstance(proxies, list):
        raise TypeError("节点源中 proxies 不是数组")
    return proxies


def _fetch(url: str) -> str:
    """下载远程订阅内容。"""
    with httpx.Client(trust_env=False, timeout=30.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


def write_smart_yaml(nodes: list[dict], output: str) -> None:
    """把排序后的节点列表写回 mihomo 格式 yaml (仅 proxies 数组)。

    节点配置原样保留，仅改变顺序。
    """
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {"proxies": nodes}
    out.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    logger.info("已输出 %d 个节点到 %s", len(nodes), out)
