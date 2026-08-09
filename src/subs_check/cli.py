"""命令行入口。"""
from __future__ import annotations

import asyncio
import logging

import click

from .config import Config


@click.group()
@click.option("--config", "-c", default="config/config.yaml", help="配置文件路径")
@click.option("--verbose", is_flag=True, help="开启调试日志")
@click.pass_context
def cli(ctx: click.Context, config: str, verbose: bool) -> None:
    """subs-check: 基于 Mihomo 内核的智能代理节点调度系统。"""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load(config)


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """启动完整调度系统。"""
    from .core.mihomo import MihomoController
    from .core.node_manager import NodeManager
    from .core.probe import ProbeEngine
    from .core.selector import SelectorService
    from .ml.features import FeatureEngine
    from .ml.model import IntelligenceEngine, NodeRanker, RealtimeScorer

    cfg = ctx.obj["config"]

    async def _run() -> None:
        mihomo = MihomoController(cfg.mihomo)
        nodes = NodeManager()
        probe = ProbeEngine(cfg.probe, nodes)
        features = FeatureEngine()
        ranker = NodeRanker(cfg.model, features)
        ranker.load()
        scorer = RealtimeScorer()
        intelligence = IntelligenceEngine(cfg.model, ranker, scorer)
        selector = SelectorService(cfg.selector, nodes, mihomo)

        await mihomo.start()
        try:
            await probe.run()
        finally:
            await probe.stop()
            await mihomo.stop()

    asyncio.run(_run())


@cli.command()
@click.pass_context
def serve(ctx: click.Context) -> None:
    """启动 FastAPI 服务。"""
    import uvicorn

    from .api import create_app

    cfg = ctx.obj["config"]
    app = create_app(cfg)
    uvicorn.run(app, host="0.0.0.0", port=8000)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
