"""mihomo-smart: 基于 Mihomo 内核的智能代理节点调度系统。

架构:
- Python 主控: 编排、特征工程、机器学习、实时评分、API 服务
- Go 嵌入: 探针引擎 (内嵌 mihomo 隧道，子进程 + HTTP)
"""

__version__ = "0.1.0"
