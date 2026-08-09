# subs-check

基于 **Mihomo 内核**的智能代理节点调度系统。

**Python 为主**（编排、特征工程、机器学习、实时评分、选择器、API 服务），**Go (mihomo) 嵌入**（内核 + 探针引擎）。

## 架构

```
节点来源 (爬虫 / 手动导入)
        |
   Node Manager
        |
   Probe Engine (Go)  +  Feature Engine (Python)
        |
        --------- Metrics ---------
        |
   Intelligence Engine (Python)
        |  LightGBM Ranker + 实时评分
        |
   Selector Service
        |
   Mihomo API (Go 内核)
        |
   用户代理流量
```

## 模块

| 模块 | 语言 | 职责 |
|------|------|------|
| Node Manager | Python | 节点生命周期管理 (NEW/PROBING/ACTIVE/BAD/DEAD) |
| Probe Engine | Go | 主动探测节点质量 (延迟/丢包/速度) |
| Feature Engine | Python | 原始指标 -> 模型特征 |
| Intelligence Engine | Python | LightGBM Ranker + 实时评分 |
| Selector Service | Python | 选择 Top N 节点并切换 mihomo |
| Mihomo Controller | Python | 通过 RESTful API 控制 mihomo 内核 |

## 快速开始

使用 [uv](https://docs.astral.sh/uv/) 作为包管理器。

```bash
# 安装依赖 (含 dev)
uv sync --extra dev

# 运行测试
uv run pytest

# 编译 Go 探针
cd go && go build -o ../bin/probe-engine .

# 运行
uv run subs-check --config config/config.yaml run
```

## 目录结构

```
├── pyproject.toml
├── src/subs_check/
│   ├── cli.py            # 命令行入口
│   ├── api.py            # FastAPI 服务
│   ├── config.py         # 配置管理
│   ├── core/
│   │   ├── mihomo.py     # mihomo 子进程 + RESTful API
│   │   ├── node_manager.py
│   │   ├── probe.py
│   │   └── selector.py
│   └── ml/
│       ├── features.py   # 特征工程
│       └── model.py      # LightGBM Ranker + 实时评分
├── go/
│   ├── go.mod
│   └── main.go           # 探针引擎
├── config/config.yaml
└── bin/                  # 编译产物
```

## 开发计划

- [ ] Phase 1: 节点管理 + 主动探测 + 实时评分 + mihomo 切换
- [ ] Phase 2: Feature Engine + LightGBM Ranker + 节点质量预测
- [ ] Phase 3: 在线学习 (Bandit 算法) + 动态节点池

## 许可证

MIT
