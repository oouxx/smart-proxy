"""FastAPI 服务: 暴露节点状态、评分、切换接口。"""
from __future__ import annotations

from fastapi import FastAPI

from .config import Config


def create_app(cfg: Config) -> FastAPI:
    """创建 FastAPI 应用。"""
    app = FastAPI(title="subs-check", version="0.1.0")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/proxies")
    async def proxies() -> dict:
        # TODO: 从 mihomo 拉取节点
        return {"proxies": []}

    @app.get("/scores")
    async def scores() -> dict:
        # TODO: 返回节点评分
        return {"scores": {}}

    return app
