FROM python:3.12-slim

WORKDIR /app

COPY . .

# 安装依赖
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com .

# 创建非 root 用户
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && mkdir -p output data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

# 生产环境使用 gunicorn，开发环境用 start.py
CMD ["gunicorn", "-c", "gunicorn.conf.py", "web:app"]
