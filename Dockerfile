# ===== Stage 1: 编译 Go 探针引擎 =====
FROM golang:1.22 AS go-builder
WORKDIR /build
# 先复制依赖清单，利用缓存
COPY go/go.mod go/go.sum ./
RUN go mod download
# 复制源码并编译 (探针引擎, 内嵌 vernesong/mihomo smart 组件, 多站点综合探测)
COPY go/main.go .
RUN CGO_ENABLED=0 go build -o /out/probe-engine .

# ===== Stage 2: Python 运行时 =====
FROM python:3.12-slim AS runtime
WORKDIR /app

# 安装 uv 包管理器
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 复制项目并安装依赖 (含项目本身)
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev

# 复制训练/模拟脚本 (探针采集 -> 训练自己的 Model.bin)
COPY scripts/ scripts/

# 复制编译好的 Go 探针引擎
COPY --from=go-builder /out/probe-engine bin/probe-engine

# 默认配置 (config/ 不入库，用模板；运行时由挂载卷覆盖)
COPY config.example.yaml config/config.yaml

# 挂载点: 配置 / 特征数据 / 模型 (含 models/smart 训练目录)
VOLUME ["/app/config", "/app/data", "/app/models"]

EXPOSE 8000

# 默认启动 HTTP 服务 (serve 内含 cron 定时 collect, 多站点探测采集训练数据)
CMD ["uv", "run", "mihomo-smart", "--config", "config/config.yaml", "serve"]
