FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# slim 基础镜像不带时区数据，缺了它 compose 的 TZ=Asia/Shanghai 会静默回落 UTC
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# 只装依赖，不做 `pip install .`：装进 site-packages 的 core/web 副本会与 /app 源码争抢导入，
# 而两者都按 Path(__file__).parent.parent 定位 data/ 与 templates/，命中副本就把库写到了卷外。
# 依赖清单仍只有 pyproject.toml 一个真源（stdlib tomllib 抽取，无需额外文件）。
COPY pyproject.toml ./
RUN python -c "import tomllib,pathlib;print('\n'.join(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies']))" > /tmp/deps.txt \
    && pip install -i "$PIP_INDEX_URL" -r /tmp/deps.txt

# 运行期所需目录逐条 COPY（改代码不再重装依赖），密钥与本地数据由 .dockerignore 挡在上下文外
COPY core/ ./core/
COPY web/ ./web/
COPY templates/ ./templates/
COPY static/ ./static/
COPY scripts/ ./scripts/
COPY benchmark/ ./benchmark/
COPY gunicorn.conf.py start.py main.py config.yaml.example ./

# 固定 UID/GID：宿主机的 ./data ./output 是 bind mount，权限按宿主 UID 走，
# 随机 UID 会在 Linux 上把 SQLite 写成 readonly（Docker Desktop 会掩盖这一点）
RUN groupadd --gid "$APP_GID" appuser \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --no-create-home --home-dir /app --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data /app/output \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

# slim 镜像里没有 curl/wget，健康检查改用 stdlib 探测
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=5).status==200 else 1)"

CMD ["gunicorn", "-c", "gunicorn.conf.py", "web:app"]
