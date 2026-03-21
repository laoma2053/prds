# ============================================
# PRDS 全合一 Dockerfile
# 单容器包含: PostgreSQL + Redis + API + Worker
# ============================================

FROM python:3.11-slim

# 安装 PostgreSQL + Redis + 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql \
    postgresql-client \
    redis-server \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# 使用 Debian 方式初始化 PostgreSQL（pg_createcluster 而非 initdb）
RUN pg_lsclusters | tail -1 | awk '{print $1}' > /tmp/pg_ver \
    && PG_VER=$(cat /tmp/pg_ver) \
    && pg_dropcluster --stop $PG_VER main 2>/dev/null || true \
    && pg_createcluster $PG_VER main -- --encoding=UTF8 --locale=C \
    && echo "host all all 0.0.0.0/0 md5" >> /etc/postgresql/$PG_VER/main/pg_hba.conf \
    && sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '127.0.0.1'/" /etc/postgresql/$PG_VER/main/postgresql.conf \
    && rm /tmp/pg_ver

# 安装 Python 依赖
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# 复制项目代码
COPY . .
RUN chmod +x entrypoint.sh

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://127.0.0.1:8000/api/v1/health'); assert r.status_code == 200"

ENTRYPOINT ["./entrypoint.sh"]
