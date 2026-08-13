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

## 快速更替场景调优

当节点池**新节点大量涌入、旧节点快速淘汰**时，离线模型因缺乏单节点历史积累而
排序能力下降，系统应更依赖实时评分，并加快节点同步与淘汰。推荐配置：

```yaml
probe:
  fast_interval_sec: 5        # 新节点快速探测间隔
  bad_after_failures: 2       # 更快降级为 BAD
  dead_after_failures: 5      # 更快淘汰为 DEAD
  recovery_successes: 2       # 恢复所需连续成功次数
model:
  model_weight: 0.4           # 降低离线模型权重
  realtime_weight: 0.6        # 提高实时评分权重 (更依赖最近探测)
  min_train_samples: 50       # 提高自动重训门槛，避免低样本噪声
node_source:
  refresh_interval_sec: 60   # 更频繁同步订阅，及时纳入新节点/淘汰旧节点
```

> 说明：`serve` 会按 `node_source.refresh_interval_sec` 周期性同步节点源——
> 新增订阅节点、淘汰已不在订阅且已降级 (BAD/DEAD) 的节点，并清理对应 Bandit 臂。
> 快速更替下建议调小该间隔，避免新节点长期不被探测、僵尸臂无限堆积。

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

## 用 mihomo smart 组件训练自己的模型

项目已把 Go 内核切换到 **vernesong/mihomo** (含 smart 组件)。smart 组件会在代理
真实流量时自动采集训练样本，可用它训练**你自己的节点**的 LightGBM 模型。

### 1. 运行代理采集真实流量

```bash
# 编译 vernesong/mihomo 二进制 (含 smart 组件)
cd go && go build -o ../bin/mihomo .

# 用 smart 代理配置启动 (引用 config/mihomo.yaml 你的节点池)
../bin/mihomo -d . -f ../config/mihomo-smart-proxy.yaml
```

把系统/应用代理指向 `mixed-port` (默认 7890)，真实流量经过 `SMART` 组后，
smart 组件把训练样本写入 `<home>/smart_weight_data.csv`。

**没有真实用户流量？用脚本模拟** (mihomo 仍需作为代理运行，脚本负责制造流量)：

```bash
# 批量通过代理访问 YouTube/Google/GitHub 等，下载/上传数据
python scripts/simulate_traffic.py --proxy http://127.0.0.1:7890 --duration 600
```

脚本会随机访问真实站点、下载测速文件、上传数据，制造 smart 组件需要的
流量特征 (download_mb/upload_mb/时长/速率等)。并发和时长可调。

### 2. 训练模型

```bash
python scripts/train_smart.py --csv <home>/smart_weight_data.csv --output <home>/Model.bin
```

脚本会计算 StandardScaler/RobustScaler 参数、训练 LightGBM 回归，并生成带
`[transforms]` 段的模型文件 (mihomo 加载时自动应用缩放)。

> 说明: 训练数据来自**真实流量遥测** (流量 MB/时长/占比等)，需积累足够样本
> (建议 ≥1000 条) 再训练。相关配置见 `config/mihomo-smart-proxy.yaml`。

### 用探针引擎采集 (推荐: 覆盖全部节点)

真实流量采集只记录**被 SMART 组选中的节点**，未选中的节点永远没样本。要覆盖全部节点，
可直接用**探针引擎**采集 (探针引擎并发对所有节点建真实隧道，不受选节点限制)：

```yaml
# config.yaml probe 段
probe:
  model_dir: models/smart        # 模型目录 (存放 smart_weight_data.csv 与 Model.bin)
  collect_csv: true              # 开启探针训练数据采集
  use_model: false               # 采集阶段先不加载模型打分
```

```bash
# 一键: 探针采集全部节点 + 训练自己的模型
python scripts/collect_and_train.py --config config/config.yaml

# 或手动分步: 先采集, 再(可选)与真实流量CSV合并, 再训练
mihomo-smart --config config/config.yaml collect   # probe_all 全部节点 -> models/smart/smart_weight_data.csv
python scripts/train_smart.py --csv models/smart/smart_weight_data.csv \
    --extra-csv <真实流量采集的CSV> --output models/smart/Model.bin
```

训练完成后把 `probe.use_model` 改回 `true`，探针引擎即用**你自己训练的模型**打分，
彻底摆脱预训练 `Model.bin`。

> 注: 探针采集的 30 个特征由 mihomo `prepareFeatures` 计算，权重标签用
> `CalculateWeight` 启发式 (与真实流量采集标签语义一致)。失败节点也会记录样本
> (weight≈0)，便于模型识别坏节点。

### 多目标综合打分 (融入 simulate_traffic 思路)

探针不再只测单一目标，而是综合**多个真实站点**打分。配置 `probe.probe_urls` 列表，
探针会并发对所有节点、对每个站点建真实隧道测延迟/丢包，汇总成综合指标
(综合延迟 = 各站点平均值，成功率 = 各站点总体可达率)，并**每个站点各采一条训练样本**
(host/geoip 特征随站点变化，让模型学到目标多样性)。

```yaml
probe:
  probe_urls:                       # 综合多个站点打分
    - "https://www.gstatic.com/generate_204"
    - "https://www.google.com/generate_204"
    - "https://www.youtube.com"
    - "https://github.com"
    - "https://www.cloudflare.com"
    - "https://www.wikipedia.org"
    - "https://www.microsoft.com"
  download_urls: []                 # 下载测速 URL 列表 (留空跳过)
  upload_urls: []                   # 上传测速 URL 列表 (留空跳过)
```

## Docker 部署

单服务架构：`serve` 进程同时提供 HTTP 下载、cron 定时 `collect`（多站点探针采集）和
在线学习。训练自己的 smart 模型也可在容器内完成。

```bash
# 1. 准备配置 (按需修改, 见下方要点)
cp config.example.yaml config/config.yaml

# 2. 准备环境变量 (按需修改端口/目录/时区)
cp .env.example .env

# 3. 构建并启动 (默认启动 serve, 带健康检查)
docker compose up -d --build

# 4. 查看状态与日志 (serve 显示 healthy 即就绪)
docker compose ps
docker compose logs -f

# 5. 下载产物
curl -O http://localhost:8000/download/smart
curl -O http://localhost:8000/download/model
```

### 环境变量 (.env)

```bash
# .env (由 .env.example 复制而来)
MIIHOMO_SMART_PORT=8000          # 宿主机端口 (容器内固定 8000)
MIIHOMO_SMART_CONTAINER=mihomo-smart
SMART_CONFIG_DIR=./config        # 数据挂载目录
SMART_DATA_DIR=./data
SMART_MODELS_DIR=./models
TZ=Asia/Shanghai                # 容器时区
```

> 健康检查: `serve` 每隔 30s 探测容器内 `http://127.0.0.1:8000/health`，
> `docker compose ps` 中 `STATUS` 显示 `(healthy)` 即服务就绪。

### 配置要点 (config.yaml)

```yaml
probe:
  probe_urls:                     # 多站点综合探测打分
    - "https://www.gstatic.com/generate_204"
    - "https://www.google.com/generate_204"
    - "https://www.youtube.com"
    - "https://github.com"
    - "https://www.cloudflare.com"
    - "https://www.wikipedia.org"
    - "https://www.microsoft.com"
  model_dir: models/smart         # 模型目录 (容器内 /app/models/smart, 即宿主 ./models/smart)
  use_model: true                 # 用自己训练的 Model.bin 打分
  collect_csv: false              # 训练阶段改为 true 以采集数据

collect:
  schedule: "0 * * * *"           # 每小时跑一次 collect (多站点探测采集)
  smart_auto_train: true          # 采集完成后自动重训 smart 模型
  smart_min_samples: 1000         # 样本数达到该值才自动训练
  smart_interval_hours: 24        # 自动重训间隔 (小时)
```

### 训练自己的模型 (在容器内)

**方式 A: 全自动 (推荐)**

```yaml
# config.yaml
probe:
  collect_csv: true        # 开启采集
  model_dir: models/smart
collect:
  smart_auto_train: true   # 每次 collect 后自动检查并重训
```

`serve` 的 cron 每小时跑一次 collect：多站点探测全部节点、把数据追加到
`./models/smart/smart_weight_data.csv`；当样本数 ≥ `smart_min_samples` 且距上次训练
超过 `smart_interval_hours` 时，自动训练 `Model.bin`。无需手动干预。

**方式 B: 手动**

```bash
# 1. 开启采集 (config.yaml 设 probe.collect_csv: true), 重启 serve
# 2. 运行一次性训练服务 (从采集数据训练 Model.bin)
docker compose run --rm train
# 3. 把 probe.use_model 改回 true 并重启
```

数据通过挂载卷持久化：`./config`、`./data`、`./models`（训练目录 `./models/smart`）。

> 说明: 用探针采集覆盖全部节点、多站点综合打分，不需要逐个手动切换节点。训练数据
> (smart_weight_data.csv) 与模型 (Model.bin) 均存放在挂载卷里，升级容器不丢失。
> 自动训练在 collect 进程内完成，训练失败/样本不足不会中断 serve。

## 许可证

MIT
