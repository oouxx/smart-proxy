"""FastAPI 服务: 暴露节点状态、评分、切换接口，以及产物下载。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .config import Config


def create_app(cfg: Config) -> FastAPI:
    """创建 FastAPI 应用。"""
    app = FastAPI(title="mihomo-smart", version="0.1.0")

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

    @app.get("/download/smart")
    async def download_smart() -> FileResponse:
        """下载 smart 命令生成的排序节点配置 (mihomo-smart.yaml)。"""
        path = Path(cfg.node_source.output)
        if not path.exists():
            raise HTTPException(404, f"文件不存在: {path}")
        return FileResponse(
            path,
            media_type="application/x-yaml",
            filename="mihomo-smart.yaml",
        )

    @app.get("/download/model")
    async def download_model() -> FileResponse:
        """下载训练好的 LightGBM 模型 (ranker.lgb)。"""
        path = Path(cfg.model.model_path)
        if not path.exists():
            raise HTTPException(404, f"文件不存在: {path}")
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename="ranker.lgb",
        )

    return app
