"""subs-check: 基于 Mihomo 内核的智能代理节点调度系统。

架构:
- Python 主控: 编排、特征工程、机器学习、实时评分、选择器、API 服务
- Go 嵌入: mihomo 内核 + 探针引擎 (子进程 + RESTful API)
"""

__version__ = "0.1.0"
