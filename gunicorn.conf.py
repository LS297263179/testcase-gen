"""Gunicorn 生产部署配置"""

import os

# 绑定地址。5000 始终监听（容器健康检查与反代回源都走它）。
# 必须是列表：配置文件里的字符串不会按逗号切分，"a,b" 会被当成单个地址解析失败。
bind = ["0.0.0.0:5000"]

# 没有独立反代时让 gunicorn 自己终结 TLS：需镜像装 pyopenssl（Dockerfile 的 PIP_EXTRA），
# 并由 TLS_CERTFILE/TLS_KEYFILE 给出证书路径，两者齐备才追加 443 的 ssl 监听。
_certfile = os.environ.get("TLS_CERTFILE", "").strip()
_keyfile = os.environ.get("TLS_KEYFILE", "").strip()
if _certfile and _keyfile:
    bind.append(f"ssl://0.0.0.0:443?certfile={_certfile}&keyfile={_keyfile}")

# Worker 模型：产品主链路是 SSE 流式生成，一个流会独占 worker 直到结束。
# sync 下并发上限就等于 worker 数（而 SQLite 单文件库又不宜多 worker 写），故用 gthread 以线程扛流。
worker_class = "gthread"
workers = 2
threads = 8

# 超时设置：一次生成可能串起数十次 LLM 调用，300s 会在流中途把 worker 掐死
timeout = 900
graceful_timeout = 30
keepalive = 75

# worker 心跳放内存盘，避免容器磁盘 IO 抖动导致误判 worker 卡死而重启它
worker_tmp_dir = "/dev/shm"

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
