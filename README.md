# mihomo-smart

基于 **Mihomo 内核**的智能代理节点调度系统。

**Python 为主**（编排、特征工程、机器学习、实时评分、API 服务），**Go (mihomo) 嵌入**（探针引擎，内嵌 mihomo 隧道）。

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
   输出排序后的节点配置 (smart/serve)
```

## 模块

| 模块 | 语言 | 职责 |
|------|------|------|
| Node Source | Python | 从本地订阅文件或远程 URL 加载节点池，输出排序后的 mihomo-smart.yaml |
| Node Manager | Python | 节点生命周期管理 (NEW/PROBING/ACTIVE/BAD/DEAD) |
| Probe Engine | Go | 主动探测节点质量 (延迟/抖动/丢包/速度)，内嵌 mihomo 隧道探测 |
| Feature Engine | Python | 原始指标 -> 模型特征 (时间窗口/节点属性) |
| Intelligence Engine | Python | LightGBM Ranker + 实时评分融合 |
| Bandit Learner | Python | 在线学习 (UCB1 + epsilon-greedy)，探索/利用平衡 |

## 快速开始

使用 [uv](https://docs.astral.sh/uv/) 作为包管理器。

```bash
# 安装依赖 (含 dev)
uv sync --extra dev

# 运行测试
uv run pytest

# 类型检查 (pyright)
uv run pyright

# 代码规范检查 (ruff)
uv run ruff check src/ tests/

# 编译 Go 探针 (需 Go 1.22+)
cd go && go build -o ../bin/probe-engine .

# 运行 (smart/serve/train/collect)
uv run mihomo-smart --config config/config.yaml smart
```

## 命令行

| 命令 | 说明 |
|------|------|
| `mihomo-smart collect` | 定时批处理: 探测所有节点 -> 提取特征 -> 追加 CSV -> 更新 Bandit (供 cron 调用) |
| `mihomo-smart smart` | 批处理: 探测节点池 -> 评分排序 -> 输出 mihomo-smart.yaml |
| `mihomo-smart train` | 用真实探测数据训练 LightGBM 模型 |
| `mihomo-smart serve` | 启动 FastAPI 服务 (端口 8000，内含 cron 定时 collect) |

## 目录结构

```
├── pyproject.toml
├── src/mihomo_smart/
│   ├── cli.py            # 命令行入口 (smart/serve/train/collect)
│   ├── api.py            # FastAPI 服务
│   ├── config.py         # 配置管理
│   ├── core/
│   │   ├── node_manager.py
│   │   ├── node_source.py # 节点源加载 + 输出 mihomo-smart.yaml
│   │   └── probe.py
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
│   ├── mihomo.template.yaml
│   └── mihomo-smart.yaml     # smart 命令输出
├── models/ranker.lgb     # 训练好的 LightGBM 模型
├── models/bandit_state.json  # Bandit 在线学习状态 (运行时生成)
├── data/features.csv     # 累积特征数据 (collect 命令每小时追加)
├── tests/                # pytest 测试
└── bin/                  # 编译产物 (probe-engine)
```

## 配置

`config/config.yaml` 包含以下区块：

| 区块 | 说明 |
|------|------|
| `probe` | 探测间隔/超时/并发、探测 URL、Go 引擎地址 |
| `model` | 模型路径、特征窗口、模型/实时评分权重 |
| `node_source` | 节点源路径/URL、输出节点数、输出文件 |
| `bandit` | 在线学习: UCB1 探索系数、epsilon-greedy 概率、状态持久化路径 |
| `collect` | 定时采集: 特征数据 CSV 输出路径 |

## 开发计划

- [x] Phase 1: 节点管理 + 主动探测 + 实时评分
- [x] Phase 2: Feature Engine + LightGBM Ranker + 节点质量预测
- [x] Phase 3: 在线学习 (Bandit 算法)

## 定时采集 (collect + cron)

`collect` 命令探测所有节点，提取特征追加到 `data/features.csv`，并更新 Bandit 在线学习状态。

### 方式一：serve 内置 cron 调度（推荐）

`serve` 进程内用 APScheduler 按 `collect.schedule`（cron 表达式）定时跑 collect，无需外部 cron：

```bash
uv run mihomo-smart serve
```

`config.yaml` 中配置 cron 表达式：

```yaml
collect:
  schedule: "0 * * * *"   # 每小时整点跑一次
```

### 方式二：宿主机 cron

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

`collect`/`smart` 在探测后，通过 **Bandit 在线学习** 对节点质量持续学习：

- **UCB1**: 上置信界算法，天然平衡探索与利用
- **epsilon-greedy**: 以一定概率随机探索，避免陷入局部最优
- **奖励反馈**: 用真实探测结果 (成功率/延迟/速度) 计算奖励，增量更新
- **持久化**: 学习状态保存到 `models/bandit_state.json`，跨会话累积

## 智能评分增强

借鉴 Smart Core 原理，评分系统包含以下增强：

- **时间衰减**: 超过 7 天未探测的节点权重逐渐降低 (λ=1/30)
- **EMA 平滑**: 实时指标用指数移动平均，近期数据权重更高，平滑异常波动
- **分阶段恢复**: BAD 节点成功先进入 PROBING 试探，连续成功 3 次才回 ACTIVE
- **智能惩罚**: 连续失败越多惩罚越大 (非线性)，失败越严重降权越明显
- **交互特征**: 延迟×丢包率、延迟×(1-成功率) 捕捉联合影响
- **模型自动更新**: collect 可定时自动重训 (默认 72 小时)
- **数据保留期**: 特征数据超过 14 天自动清理

## Docker 部署

单服务架构：`serve` 进程同时提供 HTTP 下载和 cron 定时 collect。

```bash
# 1. 准备配置
cp config.example.yaml config/config.yaml   # 按需修改

# 2. 构建并启动
docker compose up -d --build

# 3. 查看日志
docker compose logs -f

# 4. 下载产物
curl -O http://localhost:8000/download/smart
curl -O http://localhost:8000/download/model
```

数据通过挂载卷持久化：`./config`、`./data`、`./models`。

## 许可证

MIT
