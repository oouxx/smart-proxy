"""FastAPI 服务: 暴露节点状态、评分、切换接口，以及产物下载。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .config import Config
from .core.node_manager import NodeManager


def create_app(
    cfg: Config,
    node_manager: NodeManager | None = None,
    scores: dict[str, float] | None = None,
) -> FastAPI:
    """创建 FastAPI 应用。

    传入 serve 内维护的 node_manager/scores 时，/proxies 与 /scores 返回
    探针循环驱动的真实节点状态与实时评分；否则返回空。
    """
    app = FastAPI(title="mihomo-smart", version="0.1.0")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/proxies")
    async def proxies() -> dict:
        if node_manager is None:
            return {"proxies": []}
        return {
            "proxies": [
                {
                    "node_id": n.node_id,
                    "status": n.status.value,
                    "score": round(scores.get(n.node_id, 0.0), 4) if scores else None,
                }
                for n in node_manager.list()
            ]
        }

    @app.get("/scores")
    async def scores_endpoint() -> dict:
        return {"scores": scores or {}}

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
