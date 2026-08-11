#!/usr/bin/env python3
"""通过 mihomo 代理模拟真实流量，供 smart 组件采集训练数据。

前提: vernesong/mihomo 已作为代理运行 (config/mihomo-smart-proxy.yaml)，
      mixed-port 默认 7890，SMART 组已开启 collectdata: true。

本脚本批量通过代理访问真实站点 (YouTube/Google/GitHub 等)，
下载/上传数据，制造 smart 组件需要的流量特征
(download_mb/upload_mb/duration/速率/流量占比等)。

用法:
    python scripts/simulate_traffic.py --proxy http://127.0.0.1:7890 --duration 600
"""
from __future__ import annotations

import argparse
import asyncio
import random
import time

import httpx

# 目标站点 (真实可访问，产生真实流量)
TARGETS = [
    "https://www.youtube.com",
    "https://www.google.com",
    "https://github.com",
    "https://www.cloudflare.com",
    "https://www.wikipedia.org",
    "https://www.reddit.com",
    "https://www.microsoft.com",
    "https://www.apple.com",
    "https://www.amazon.com",
    "https://www.netflix.com",
    "https://www.bing.com",
    "https://www.baidu.com",
]

# 下载测速文件 (产生较大 download_mb)
DOWNLOAD_URLS = [
    "https://speed.cloudflare.com/__down?bytes=5000000",   # 5MB
    "https://speed.cloudflare.com/__down?bytes=10000000",  # 10MB
    "https://proof.ovh.net/files/10Mb.dat",                # 10MB
    "https://proof.ovh.net/files/100Mb.dat",               # 100MB
]


async def download(client: httpx.AsyncClient, url: str, max_bytes: int) -> None:
    """下载内容，流式读取以产生 download_mb 和连接时长。"""
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                return
            read = 0
            async for chunk in resp.aiter_bytes():
                read += len(chunk)
                if read >= max_bytes:
                    break
    except httpx.HTTPError:
        pass


async def upload(client: httpx.AsyncClient, url: str, size: int) -> None:
    """上传数据以产生 upload_mb。"""
    try:
        payload = b"x" * size
        await client.post(url, content=payload)
    except httpx.HTTPError:
        pass


async def worker(
    client: httpx.AsyncClient,
    targets: list[str],
    download_urls: list[str],
    stop: asyncio.Event,
) -> None:
    """单个并发任务: 随机访问站点 + 下载/上传。"""
    while not stop.is_set():
        # 1. 随机访问一个站点 (产生连接 + 少量流量)
        url = random.choice(targets)
        try:
            async with client.stream("GET", url) as resp:
                if resp.status_code < 400:
                    read = 0
                    async for chunk in resp.aiter_bytes():
                        read += len(chunk)
                        if read >= 200_000:  # 200KB 页面
                            break
        except httpx.HTTPError:
            pass

        # 2. 随机下载一个测速文件 (产生较大 download_mb + 时长)
        if random.random() < 0.5:
            dl = random.choice(download_urls)
            await download(client, dl, random.randint(1_000_000, 20_000_000))

        # 3. 随机上传 (产生 upload_mb)
        if random.random() < 0.3:
            await upload(client, random.choice(targets), random.randint(50_000, 500_000))

        # 随机间隔，模拟真实用户行为
        await asyncio.sleep(random.uniform(0.5, 3.0))


async def main() -> None:
    parser = argparse.ArgumentParser(description="模拟真实流量供 smart 组件采集")
    parser.add_argument("--proxy", default="http://127.0.0.1:7890",
                        help="mihomo 代理地址 (mixed-port)")
    parser.add_argument("--duration", type=int, default=600,
                        help="模拟时长 (秒)")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="并发连接数")
    args = parser.parse_args()

    timeout = httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=15.0)
    async with httpx.AsyncClient(
        proxy=args.proxy,
        timeout=timeout,
        trust_env=False,
        follow_redirects=True,
    ) as client:
        stop = asyncio.Event()
        tasks = [
            asyncio.create_task(
                worker(client, TARGETS, DOWNLOAD_URLS, stop)
            )
            for _ in range(args.concurrency)
        ]

        print(f"开始模拟流量: {args.duration}s, 并发 {args.concurrency}, 代理 {args.proxy}")
        start = time.time()
        try:
            while time.time() - start < args.duration:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("手动中断")
        finally:
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)

        print(f"模拟完成，耗时 {time.time() - start:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
