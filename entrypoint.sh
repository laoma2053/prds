#!/bin/bash
# ============================================
# PRDS 全合一启动脚本
# 在单个容器内启动: PostgreSQL + Redis + API + Worker
# ============================================

set -e

echo "🚀 PRDS 全合一容器启动中..."

# ── 1. 启动 PostgreSQL ────────────────────
echo "📦 启动 PostgreSQL..."
PG_VER=$(pg_lsclusters -h | awk '{print $1}' | head -1)

# 绑定挂载场景：首次启动时 data 目录为空，需要重新创建 cluster
if [ ! -d "/var/lib/postgresql/${PG_VER}/main" ]; then
    echo "📝 首次挂载，初始化 PostgreSQL cluster..."
    pg_dropcluster --stop $PG_VER main 2>/dev/null || true
    pg_createcluster $PG_VER main
fi

pg_ctlcluster $PG_VER main start

# 等待 PostgreSQL 就绪
for i in $(seq 1 30); do
    if pg_isready -q 2>/dev/null; then
        echo "✅ PostgreSQL ${PG_VER} 就绪"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "❌ PostgreSQL 启动超时"
        exit 1
    fi
    sleep 1
done

# 首次启动时创建数据库和用户
su postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='$POSTGRES_USER'\" | grep -q 1" 2>/dev/null || {
    echo "📝 创建数据库用户: $POSTGRES_USER"
    su postgres -c "psql -c \"CREATE USER $POSTGRES_USER WITH PASSWORD '$POSTGRES_PASSWORD';\""
    su postgres -c "psql -c \"CREATE DATABASE $POSTGRES_DB OWNER $POSTGRES_USER;\""
    su postgres -c "psql -c \"GRANT ALL PRIVILEGES ON DATABASE $POSTGRES_DB TO $POSTGRES_USER;\""
}

# ── 2. 启动 Redis ─────────────────────────
echo "📦 启动 Redis..."
redis-server --daemonize yes --maxmemory 1gb --maxmemory-policy allkeys-lru
echo "✅ Redis 就绪"

# ── 3. 数据库迁移（自动执行）──────────────
echo "📋 执行数据库迁移..."
cd /app
# 如果没有迁移文件，先自动生成
if [ -z "$(ls -A /app/migrations/versions/*.py 2>/dev/null)" ]; then
    echo "📝 首次部署，自动生成迁移文件..."
    alembic revision --autogenerate -m "auto_init" 2>/dev/null || echo "⚠️ 迁移文件生成失败"
fi
# 执行迁移（已执行过的会自动跳过）
alembic upgrade head 2>/dev/null && echo "✅ 数据库迁移完成" || echo "⚠️ 数据库迁移跳过"

# ── 4. 启动 Worker（后台运行）──────────────
echo "🔧 启动 Worker..."
python -m app.workers.main &
WORKER_PID=$!
echo "✅ Worker 启动 (PID: $WORKER_PID)"

# ── 5. 启动 API（前台运行，作为主进程）────
echo "🌐 启动 API 服务..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 8
