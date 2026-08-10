"""命令行入口。"""
from __future__ import annotations

import asyncio
import csv
import logging
import time
from itertools import zip_longest

import click

from .config import Config
from .core.node_manager import NodeStatus

logger = logging.getLogger(__name__)


# 连续失败多少次判定为坏节点 (降级到 BAD)
_BAD_AFTER_FAILURES = 3
# 连续失败多少次判定为死节点 (降级到 DEAD，随后被动态池淘汰)
_DEAD_AFTER_FAILURES = 10
# 分阶段恢复: BAD 节点成功后再连续成功多少次才回 ACTIVE
_RECOVERY_SUCCESSES = 3


def _bandit_reward(history: list) -> float:
    """根据探测历史计算 Bandit 奖励 (0~1)。

    奖励 = 成功率 + 延迟/速度加权，作为在线学习的真实反馈。
    """
    if not history:
        return 0.0
    success_rate = sum(1 for r in history if r.success) / len(history)
    latencies = [r.latency_ms for r in history if r.latency_ms is not None]
    speeds = [r.download_speed_kbps for r in history if r.download_speed_kbps is not None]
    latency_score = 0.0
    if latencies:
        avg = sum(latencies) / len(latencies)
        latency_score = max(0.0, 1.0 - avg / 200.0)
    speed_score = 0.0
    if speeds:
        avg = sum(speeds) / len(speeds)
        speed_score = min(1.0, avg / 10_000.0)
    return max(0.0, min(1.0, 0.5 * success_rate + 0.3 * latency_score + 0.2 * speed_score))


def _apply_node_status(nodes, probe) -> None:
    """根据最近探测结果驱动节点状态机迁移。

    NEW -> PROBING (首次探测) -> ACTIVE (首次成功)
    ACTIVE/BAD -> BAD (连续多次失败)
    """
    for node in nodes.list():
        history = probe.history(node.node_id)
        if not history:
            continue
        latest = history[-1]
        if node.status == NodeStatus.NEW:
            nodes.set_status(node.node_id, NodeStatus.PROBING)
        elif node.status in (NodeStatus.PROBING, NodeStatus.BAD):
            if latest.success:
                if node.status == NodeStatus.BAD:
                    # 分阶段恢复: BAD 成功先进入 PROBING 试探
                    nodes.set_status(node.node_id, NodeStatus.PROBING)
                else:
                    # PROBING 连续成功 _RECOVERY_SUCCESSES 次后回 ACTIVE
                    recent = history[-_RECOVERY_SUCCESSES:]
                    if len(recent) >= _RECOVERY_SUCCESSES and all(
                        r.success for r in recent
                    ):
                        nodes.set_status(node.node_id, NodeStatus.ACTIVE)
            elif len(history) >= _DEAD_AFTER_FAILURES and all(
                not r.success for r in history[-_DEAD_AFTER_FAILURES:]
            ):
                # 连续失败过多 -> DEAD，由动态池淘汰
                nodes.set_status(node.node_id, NodeStatus.DEAD)
            elif len(history) >= _BAD_AFTER_FAILURES and all(
                not r.success for r in history[-_BAD_AFTER_FAILURES:]
            ):
                nodes.set_status(node.node_id, NodeStatus.BAD)
        elif node.status == NodeStatus.ACTIVE and not latest.success and (
            len(history) >= _BAD_AFTER_FAILURES
            and all(not r.success for r in history[-_BAD_AFTER_FAILURES:])
        ):
            nodes.set_status(node.node_id, NodeStatus.BAD)


@click.group()
@click.option("--config", "-c", default="config/config.yaml", help="配置文件路径")
@click.option("--verbose", is_flag=True, help="开启调试日志")
@click.pass_context
def cli(ctx: click.Context, config: str, verbose: bool) -> None:
    """mihomo-smart: 基于 Mihomo 内核的智能代理节点调度系统。"""
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
    from .core.pool import DynamicPool
    from .core.probe import ProbeEngine
    from .core.selector import SelectorService
    from .ml.bandit import BanditLearner
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
        # Phase 3: 在线学习 + 动态节点池
        bandit = BanditLearner(
            alpha=cfg.bandit.alpha,
            exploration_rate=cfg.bandit.exploration_rate,
            min_selections=cfg.bandit.min_selections,
        )
        bandit.load(cfg.bandit.state_path)
        pool = DynamicPool(cfg.pool, nodes)

        async def on_round() -> None:
            """每轮探测后: 同步 -> 注入/修剪 -> 状态迁移 -> 评分 -> Bandit 选择 -> 切换。"""
            await nodes.sync_from_mihomo(mihomo)
            pool.inject_from_source(cfg.node_source)
            _apply_node_status(nodes, probe)
            pool.prune(probe, bandit)
            candidates = nodes.list(NodeStatus.ACTIVE) + nodes.list(NodeStatus.PROBING)
            if not candidates:
                logger.info("当前无可调度节点，等待节点源注入")
                return
            scores: dict[str, float] = {}
            for node in candidates:
                history = probe.history(
                    node.node_id, window_sec=cfg.model.feature_window_min * 60
                )
                scores[node.node_id] = intelligence.score_node(node, history)
            logger.info("本轮评分: %s", scores)

            # Bandit 在线学习: 在 Top 候选里做探索/利用
            top_candidates = selector.select_top(scores)
            if not top_candidates:
                logger.info("没有满足最低评分的节点")
                return
            chosen = bandit.select(top_candidates, top_k=1)[0]
            logger.info("Bandit 选择节点: %s (候选: %s)", chosen, top_candidates)
            await selector.apply(scores, force=chosen)

            # 用本轮探测结果更新 Bandit 奖励并持久化
            for node_id in top_candidates:
                history = probe.history(
                    node_id, window_sec=cfg.bandit.reward_window_min * 60
                )
                bandit.update(node_id, _bandit_reward(history))
            bandit.save(cfg.bandit.state_path)

        await mihomo.start()
        await probe.start_engine()
        try:
            await probe.run(on_round=on_round)
        finally:
            await probe.stop()
            await probe.stop_engine()
            await mihomo.stop()

    asyncio.run(_run())


@cli.command()
@click.pass_context
def smart(ctx: click.Context) -> None:
    """批处理: 探测节点池 -> 评分排序 -> 输出 mihomo-smart.yaml。"""
    from .core.node_manager import NodeManager, ProxyNode
    from .core.node_source import load_nodes, write_smart_yaml
    from .core.probe import ProbeEngine
    from .ml.features import FeatureEngine
    from .ml.model import IntelligenceEngine, NodeRanker, RealtimeScorer

    cfg = ctx.obj["config"]

    async def _smart() -> None:
        # 1. 读取节点源 (本地文件或远程 URL)
        nodes = load_nodes(cfg.node_source)
        logger.info("节点源加载 %d 个节点", len(nodes))

        # 2. 批量探测 (Go 引擎并发遍历节点池)
        engine = ProbeEngine(cfg.probe, NodeManager())
        await engine.start_engine()
        try:
            results = await engine.probe_all(nodes)
        finally:
            await engine.stop_engine()

        # 3. 评分排序
        features = FeatureEngine()
        ranker = NodeRanker(cfg.model, features)
        ranker.load()
        scorer = RealtimeScorer()
        intelligence = IntelligenceEngine(cfg.model, ranker, scorer)

        scored: list[tuple[float, dict]] = []
        for node_cfg, result in zip(nodes, results):
            node = ProxyNode(
                node_id=result.node_id,
                server="",
                port=0,
                protocol=str(node_cfg.get("type", "unknown")).lower(),
            )
            score = intelligence.score_node(node, [result])
            scored.append((score, node_cfg))

        scored.sort(key=lambda kv: kv[0], reverse=True)
        top = [
            node_cfg
            for score, node_cfg in scored
            if score >= cfg.node_source.min_score
        ][: cfg.node_source.top_n]
        logger.info("评分完成，取 Top %d (共 %d 个达标)", len(top), len(scored))

        # 4. 输出排序后的 mihomo-smart.yaml
        write_smart_yaml(top, cfg.node_source.output)

    asyncio.run(_smart())


@cli.command()
@click.pass_context
def train(ctx: click.Context) -> None:
    """用真实探测数据训练 LightGBM 模型。"""
    from .core.node_manager import NodeManager, ProxyNode
    from .core.node_source import load_nodes
    from .core.probe import ProbeEngine
    from .ml.features import FeatureEngine
    from .ml.model import NodeRanker, quality_label

    cfg = ctx.obj["config"]

    async def _train() -> None:
        # 1. 读取节点源 (to_thread 避免同步 HTTP 阻塞事件循环)
        nodes = await asyncio.to_thread(load_nodes, cfg.node_source)
        logger.info("节点源加载 %d 个节点", len(nodes))

        # 2. 批量探测 (真实数据)
        engine = ProbeEngine(cfg.probe, NodeManager())
        await engine.start_engine()
        try:
            results = await engine.probe_all(nodes)
        finally:
            await engine.stop_engine()

        # 3. 构建特征 + 标签 (zip_longest 避免结果缺失时静默截断)
        features = FeatureEngine()
        X: list[list[float]] = []
        y: list[float] = []
        for node_cfg, result in zip_longest(nodes, results):
            if result is None:
                logger.warning("节点 %s 无探测结果，跳过", node_cfg.get("name"))
                continue
            node = ProxyNode(
                node_id=result.node_id,
                server="",
                port=0,
                protocol=str(node_cfg.get("type", "unknown")).lower(),
            )
            feats = features.build_features(node, [result])
            X.append([feats[name] for name in features.feature_names()])
            y.append(quality_label(result))
        logger.info("训练样本: %d 个", len(X))

        # 4. 训练并保存
        ranker = NodeRanker(cfg.model, features)
        ranker.train(X, y, feature_names=features.feature_names())

        # 5. 打印特征重要性
        if ranker._model is not None:
            imp = sorted(
                zip(features.feature_names(), ranker._model.feature_importances_),
                key=lambda kv: kv[1],
                reverse=True,
            )
            logger.info("特征重要性 (Top 5): %s", imp[:5])

    asyncio.run(_train())


def _append_csv(path: str, rows: list[dict], feature_names: list[str]) -> None:
    """把特征行追加到 CSV (首次创建时写表头)。

    用临时文件 + os.replace 保证原子性，避免中途失败留下半行数据。
    """
    import os
    import shutil
    from pathlib import Path

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # 检查特征名与保留列冲突
    reserved = {
        "timestamp",
        "node_id",
        "protocol_name",
        "country_name",
        "score",
        "bandit_reward",
        "bandit_ucb_score",
    }
    conflict = reserved & set(feature_names)
    if conflict:
        raise ValueError(f"特征名与保留列冲突: {conflict}")

    fieldnames = (
        ["timestamp", "node_id", "protocol_name", "country_name"]
        + feature_names
        + ["score", "bandit_reward", "bandit_ucb_score"]
    )
    write_header = not out.exists()
    tmp = out.with_suffix(out.suffix + ".tmp")
    # 若原文件已存在，先复制到临时文件再追加，保证原子替换
    if not write_header:
        shutil.copy2(out, tmp)
    with open(tmp, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, out)


def _prune_csv(path: str, retention_days: int) -> int:
    """清理超过保留期的特征数据 (按 timestamp 列)。返回移除的行数。"""
    import os
    from datetime import datetime
    from pathlib import Path

    out = Path(path)
    if not out.exists():
        return 0
    cutoff = time.time() - retention_days * 86400

    def _row_ts(ts: str) -> float:
        try:
            # 时间戳以本地时间写入，按本地时区解释 (有意的 naive datetime)
            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timestamp()  # noqa: DTZ007
        except (ValueError, TypeError):
            return 0.0

    with open(out, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if fieldnames is None:
            return 0  # 空文件或异常，跳过清理
        all_rows = list(reader)
    kept = [r for r in all_rows if _row_ts(r.get("timestamp", "")) >= cutoff]
    removed = len(all_rows) - len(kept)
    if removed:
        tmp = out.with_suffix(out.suffix + ".tmp")
        with open(tmp, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(kept)
        os.replace(tmp, out)
        logger.info("数据保留期清理: 移除 %d 行 (保留 %d 行)", removed, len(kept))
    return removed


def _should_retrain(model_path: str, interval_hours: int) -> bool:
    """判断是否需要自动重训模型 (模型不存在或超过间隔)。"""
    from pathlib import Path

    p = Path(model_path)
    if not p.exists():
        return True
    age_hours = (time.time() - p.stat().st_mtime) / 3600
    return age_hours >= interval_hours


async def run_collect(cfg, out_path: str | None = None) -> None:
    """collect 核心逻辑: 探测 -> 特征 -> 追加 CSV -> 更新 Bandit。

    可被 collect 命令直接调用，也可被 serve 内的 cron 调度器定时调用。
    """
    from .core.node_manager import NodeManager, ProxyNode
    from .core.node_source import load_nodes
    from .core.probe import ProbeEngine
    from .ml.bandit import BanditLearner
    from .ml.features import FeatureEngine
    from .ml.model import IntelligenceEngine, NodeRanker, RealtimeScorer, quality_label

    out_path = out_path or cfg.collect.output

    # 1. 读取节点源 (to_thread 避免同步 HTTP 阻塞事件循环)
    nodes = await asyncio.to_thread(load_nodes, cfg.node_source)
    logger.info("节点源加载 %d 个节点", len(nodes))

    # 2. 批量探测 (probe-engine 内嵌 mihomo 隧道)
    engine = ProbeEngine(cfg.probe, NodeManager())
    await engine.start_engine()
    try:
        results = await engine.probe_all(nodes)
    finally:
        await engine.stop_engine()

    # 3. 特征 + 评分 + Bandit 在线学习
    features = FeatureEngine()
    ranker = NodeRanker(cfg.model, features)
    ranker.load()
    scorer = RealtimeScorer()
    intelligence = IntelligenceEngine(cfg.model, ranker, scorer)
    bandit = BanditLearner(
        alpha=cfg.bandit.alpha,
        exploration_rate=cfg.bandit.exploration_rate,
        min_selections=cfg.bandit.min_selections,
    )
    bandit.load(cfg.bandit.state_path)

    # 先收集所有节点数据并更新 Bandit，避免 zip 静默截断
    pending: list[tuple[ProxyNode, dict, float, float]] = []
    X_train: list[list[float]] = []
    y_train: list[float] = []
    for node_cfg, result in zip_longest(nodes, results):
        if result is None:
            logger.warning("节点 %s 无探测结果，跳过", node_cfg.get("name"))
            continue
        node = ProxyNode(
            node_id=result.node_id,
            server="",
            port=0,
            protocol=str(node_cfg.get("type", "unknown")).lower(),
            country=node_cfg.get("country", ""),
        )
        feats = features.build_features(node, [result])
        score = intelligence.score_node(node, [result])
        reward = _bandit_reward([result])
        bandit.update(result.node_id, reward)
        pending.append((node, feats, score, reward))
        # 收集训练样本 (用于模型自动更新)
        X_train.append([feats[name] for name in features.feature_names()])
        y_train.append(quality_label(result))

    # 全部 update 完再统一计算 UCB 分数 (保证基准一致)
    rows: list[dict] = []
    for node, feats, score, reward in pending:
        rows.append(
            {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "node_id": node.node_id,
                "protocol_name": node.protocol,
                "country_name": node.country,
                **feats,
                "score": round(score, 4),
                "bandit_reward": round(reward, 4),
                "bandit_ucb_score": round(bandit.ucb_score(node.node_id), 4),
            }
        )

    # 4. 追加到 CSV + 数据保留期清理 + 持久化 Bandit 状态
    _append_csv(out_path, rows, features.feature_names())
    _prune_csv(out_path, cfg.collect.retention_days)
    bandit.save(cfg.bandit.state_path)

    # 5. 模型自动更新 (可选)
    if cfg.model.auto_train and _should_retrain(
        cfg.model.model_path, cfg.model.auto_train_interval_hours
    ):
        if len(X_train) >= 10:
            ranker.train(X_train, y_train, feature_names=features.feature_names())
            logger.info("模型自动重训完成 (%d 个样本)", len(X_train))
        else:
            logger.info("样本不足 (%d)，跳过自动重训", len(X_train))

    logger.info("完成: %d 个节点特征已追加到 %s (Bandit 已更新)", len(rows), out_path)


@cli.command()
@click.pass_context
@click.option("--output", default=None, help="覆盖配置中的特征 CSV 输出路径")
def collect(ctx: click.Context, output: str | None) -> None:
    """定时批处理: 探测所有节点 -> 提取特征 -> 追加 CSV -> 更新 Bandit。

    供 cron 每小时调用一次，累积特征数据并持续在线学习。
    """
    cfg = ctx.obj["config"]
    asyncio.run(run_collect(cfg, output))


@cli.command()
@click.pass_context
def serve(ctx: click.Context) -> None:
    """启动 FastAPI 服务 (含 cron 定时 collect)。"""
    import uvicorn
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from .api import create_app

    cfg = ctx.obj["config"]
    app = create_app(cfg)

    def _run_collect_sync() -> None:
        # BackgroundScheduler 在线程中运行，job 内用 asyncio.run 跑 async 逻辑
        asyncio.run(run_collect(cfg))

    # 在 serve 进程内按 cron 表达式定时跑 collect (线程调度器，不阻塞主循环)
    scheduler = BackgroundScheduler()
    if cfg.collect.schedule:
        scheduler.add_job(
            _run_collect_sync,
            CronTrigger.from_crontab(cfg.collect.schedule),
        )
        scheduler.start()
        logger.info("已调度 collect: cron=%s", cfg.collect.schedule)

    uvicorn.run(app, host="0.0.0.0", port=8000)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
