FROM python:3.12-slim

WORKDIR /app

# 依赖层（缓存友好：先 COPY 依赖清单，再安装）
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt

# 源码层
COPY . .

# 创建非 root 用户
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && mkdir -p output data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

# 生产环境使用 gunicorn，开发环境用 start.py
CMD ["gunicorn", "-c", "gunicorn.conf.py", "web:app"]
