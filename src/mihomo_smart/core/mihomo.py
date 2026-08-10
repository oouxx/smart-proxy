"""Mihomo Controller: 通过 subprocess 启动 mihomo 内核，并用 RESTful API 控制。

mihomo 的 external-controller 提供 RESTful API:
- GET  /proxies            获取所有代理节点
- GET  /proxies/{name}     获取单个节点信息 (含延迟/健康检查)
- PUT  /proxies/{name}     切换当前节点
- GET  /version            获取版本
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from ..config import MihomoConfig

logger = logging.getLogger(__name__)

# mihomo/clash 中代理组的类型 (这些不是真正的节点)
_GROUP_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance", "Relay", "Compatible"}
_BUILTIN_TYPES = {"Direct", "Reject", "Global"}


class MihomoError(RuntimeError):
    """mihomo 相关错误。"""


class MihomoController:
    """管理 mihomo 子进程并与其 RESTful API 交互。"""

    def __init__(self, cfg: MihomoConfig) -> None:
        self.cfg = cfg
        self._proc: asyncio.subprocess.Process | None = None
        self._base_url = f"http://{cfg.external_controller}"
        self._headers = {"Authorization": f"Bearer {cfg.secret}"} if cfg.secret else {}

    async def start(self) -> None:
        """启动 mihomo 子进程。"""
        if self._proc and self._proc.returncode is None:
            return
        binary = Path(self.cfg.binary)
        if not binary.exists():
            raise MihomoError(
                f"mihomo 内核二进制不存在: {self.cfg.binary}。"
                "请编译或下载 mihomo 后放入 bin/ 目录。"
            )
        logger.info("启动 mihomo: %s", self.cfg.binary)
        self._proc = await asyncio.create_subprocess_exec(
            self.cfg.binary,
            "-d",
            ".",
            "-f",
            self.cfg.config_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 等待 API 就绪
        for _ in range(50):
            if await self._ping():
                return
            await asyncio.sleep(0.2)
        raise MihomoError("mihomo API 未在预期时间内就绪")

    async def stop(self) -> None:
        """停止 mihomo 子进程。"""
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None

    async def _ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                r = await client.get(f"{self._base_url}/version", headers=self._headers)
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def get_proxies(self) -> dict[str, Any]:
        """获取所有代理节点。"""
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self._base_url}/proxies", headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def get_nodes(self) -> list[dict[str, str]]:
        """从内核获取真正的代理节点列表 (排除代理组与内置项)。

        mihomo 才是节点列表的唯一数据源 (由 proxy-providers 维护)，
        这里从 API 拉取，Python 侧不再自行维护节点成员。
        """
        data = await self.get_proxies()
        nodes: list[dict[str, str]] = []
        for name, info in data.get("proxies", {}).items():
            ptype = info.get("type", "")
            if ptype in _GROUP_TYPES or ptype in _BUILTIN_TYPES:
                continue
            nodes.append({"node_id": name, "protocol": ptype.lower()})
        return nodes

    async def get_proxy(self, name: str) -> dict[str, Any]:
        """获取单个节点信息 (含延迟、历史健康检查)。"""
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self._base_url}/proxies/{name}", headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def switch_proxy(self, group: str, node: str) -> None:
        """切换代理组当前节点。"""
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.put(
                f"{self._base_url}/proxies/{group}",
                headers=self._headers,
                json={"name": node},
            )
            r.raise_for_status()
