"""Gunicorn 生产部署配置"""

import multiprocessing

# 绑定地址
bind = "0.0.0.0:5000"

# Worker 数量（SQLite 单文件数据库，不宜太多 worker）
# 建议 2-4 个 worker，避免 SQLite 写冲突
workers = min(4, multiprocessing.cpu_count())

# Worker 类型：sync 适合 SQLite（每个请求独立处理）
# 如果需要更好的并发，可以用 gevent（需安装 gevent）
worker_class = "sync"

# 超时设置（LLM 调用可能耗时较长）
timeout = 300  # 5 分钟
graceful_timeout = 30

# 请求大小限制（与 Flask 的 MAX_CONTENT_LENGTH 一致）
limit_request_line = 0
limit_request_fields = 100
limit_request_field_size = 0

# 日志
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 进程名
proc_name = "testcase-gen"

# 预加载应用（减少内存占用）
preload_app = True
