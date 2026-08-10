# mihomo-smart

基于 **Mihomo 内核**的智能代理节点调度系统。

**Python 为主**（编排、特征工程、机器学习、实时评分、选择器、API 服务），**Go (mihomo) 嵌入**（内核 + 探针引擎）。

## 架构

```
节点来源 (本地订阅文件 / 远程 URL)
        |
   Node Source (Python)
        |
   Node Manager (Python)
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
| Node Source | Python | 从本地订阅文件或远程 URL 加载节点池，输出排序后的 mihomo-smart.yaml |
| Node Manager | Python | 节点生命周期管理 (NEW/PROBING/ACTIVE/BAD/DEAD)，与 mihomo 内核同步成员 |
| Dynamic Pool | Python | 动态节点池: 从节点源注入新节点，淘汰低分/DEAD 节点 |
| Probe Engine | Go | 主动探测节点质量 (延迟/抖动/丢包/速度)，支持 mihomo 隧道探测与直连 fallback |
| Feature Engine | Python | 原始指标 -> 模型特征 (时间窗口/节点属性) |
| Intelligence Engine | Python | LightGBM Ranker + 实时评分融合 |
| Bandit Learner | Python | 在线学习 (UCB1 + epsilon-greedy)，探索/利用平衡 |
| Selector Service | Python | 选择 Top N 节点并切换 mihomo |
| Mihomo Controller | Python | 通过 RESTful API 控制 mihomo 内核 |

## 快速开始

使用 [uv](https://docs.astral.sh/uv/) 作为包管理器。

```bash
# 安装依赖 (含 dev)
uv sync --extra dev

# 运行测试
uv run pytest

# 编译 Go 探针 (需 Go 1.22+)
cd go && go build -o ../bin/probe-engine .

# 运行
uv run mihomo-smart --config config/config.yaml run
```

## 命令行

| 命令 | 说明 |
|------|------|
| `mihomo-smart run` | 启动完整调度系统 (探测 -> 状态迁移 -> 评分 -> 切换 mihomo) |
| `mihomo-smart collect` | 定时批处理: 探测所有节点 -> 提取特征 -> 追加 CSV -> 更新 Bandit (供 cron 调用) |
| `mihomo-smart smart` | 批处理: 探测节点池 -> 评分排序 -> 输出 mihomo-smart.yaml |
| `mihomo-smart train` | 用真实探测数据训练 LightGBM 模型 |
| `mihomo-smart serve` | 启动 FastAPI 服务 (端口 8000) |

## 目录结构

```
├── pyproject.toml
├── src/mihomo_smart/
│   ├── cli.py            # 命令行入口 (run/smart/train/serve)
│   ├── api.py            # FastAPI 服务
│   ├── config.py         # 配置管理
│   ├── core/
│   │   ├── mihomo.py     # mihomo 子进程 + RESTful API
│   │   ├── node_manager.py
│   │   ├── node_source.py # 节点源加载 + 输出 mihomo-smart.yaml
│   │   ├── pool.py       # 动态节点池 (注入/淘汰/修剪)
│   │   ├── probe.py
│   │   └── selector.py
│   └── ml/
│       ├── bandit.py    # 在线学习 (UCB1 + epsilon-greedy)
│       ├── features.py   # 特征工程
│       └── model.py      # LightGBM Ranker + 实时评分
├── go/
│   ├── go.mod
│   └── main.go           # 探针引擎 (HTTP 服务: /health /probe /probe_all)
├── config/
│   ├── config.yaml       # 主配置
│   ├── mihomo.yaml       # 节点订阅文件
│   ├── mihomo.template.yaml  # mihomo 内核模板
│   └── mihomo-smart.yaml     # smart 命令输出
├── models/ranker.lgb     # 训练好的 LightGBM 模型
├── models/bandit_state.json  # Bandit 在线学习状态 (运行时生成)
├── data/features.csv     # 累积特征数据 (collect 命令每小时追加)
├── tests/                # pytest 测试
└── bin/                  # 编译产物 (mihomo, probe-engine)
```

## 配置

`config/config.yaml` 包含以下区块：

| 区块 | 说明 |
|------|------|
| `mihomo` | 内核二进制、模板配置、外部控制器地址 |
| `probe` | 探测间隔/超时/并发、探测 URL、Go 引擎地址 |
| `model` | 模型路径、特征窗口、模型/实时评分权重 |
| `selector` | Top N、最低评分、代理组名 |
| `node_source` | 节点源路径/URL、输出节点数、输出文件 |
| `bandit` | 在线学习: UCB1 探索系数、epsilon-greedy 概率、状态持久化路径 |
| `pool` | 动态节点池: 上限、修剪间隔、淘汰成功率阈值 |
| `collect` | 定时采集: 特征数据 CSV 输出路径 |

## 开发计划

- [x] Phase 1: 节点管理 + 主动探测 + 实时评分 + mihomo 切换
- [x] Phase 2: Feature Engine + LightGBM Ranker + 节点质量预测
- [x] Phase 3: 在线学习 (Bandit 算法) + 动态节点池

## 定时采集 (collect + cron)

`collect` 命令每小时探测所有节点，提取特征追加到 `data/features.csv`，并更新 Bandit 在线学习状态。用 cron 定时触发：

```cron
# 每小时整点运行一次
0 * * * * cd /path/to/mihomo-smart-rust && uv run mihomo-smart --config config/config.yaml collect >> /tmp/mihomo-smart.log 2>&1
```

`data/features.csv` 累积格式 (每小时追加一行/节点)：

```
timestamp,node_id,protocol,country_name,latency_avg_5m,...,score,bandit_reward,bandit_ucb_score
```

累积的数据可直接用于 LightGBM 训练，同时 Bandit 状态跨任务累积，持续学习节点真实质量。

## 在线学习 (Phase 3)

`run` 命令在每轮探测后，除了静态评分，还会通过 **Bandit 在线学习** 在候选节点间做探索/利用决策：

- **UCB1**: 上置信界算法，天然平衡探索与利用
- **epsilon-greedy**: 以一定概率随机探索，避免陷入局部最优
- **奖励反馈**: 用真实探测结果 (成功率/延迟/速度) 计算奖励，增量更新
- **持久化**: 学习状态保存到 `models/bandit_state.json`，跨会话累积

同时 **动态节点池** 让节点池随真实表现伸缩：

- **注入**: 从节点源 (本地/远程) 拉取新节点加入池
- **淘汰**: 连续失败节点降级 BAD -> DEAD 并移出
- **修剪**: 定期清理长期低成功率节点，控制池规模

## 许可证

MIT
