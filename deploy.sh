#!/bin/bash
#使用方式：chmod +x deploy.sh && ./deploy.sh

set -e

# ── 1. 检查 Docker 环境 ──────────────────────────────────────────
command -v docker &>/dev/null || { echo "错误: 未安装 Docker"; exit 1; }
docker compose version &>/dev/null || { echo "错误: 未安装 Docker Compose"; exit 1; }

# ── 2. 准备 .env ─────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  echo "已生成 .env，请修改以下必填项后重新运行:"
  echo "  APP_SECRET_KEY   - 应用密钥（随机字符串）"
  echo "  POSTGRES_PASSWORD - 数据库密码"
  echo "  ADMIN_PASSWORD   - 管理后台密码"
  echo "  PANSOU_BASE_URL  - PanSou 服务地址（容器名:端口）"
  exit 1
fi

# 检查生产环境必改项是否仍为默认值
for key in APP_SECRET_KEY POSTGRES_PASSWORD ADMIN_PASSWORD; do
  val=$(grep "^${key}=" .env | cut -d= -f2-)
  if [[ "$val" == "change-me-in-production" || "$val" == "prds_secret" || "$val" == "admin789" ]]; then
    echo "警告: ${key} 仍为默认值，生产环境请务必修改"
  fi
done

# ── 3. 确认 PanSou 网络 ──────────────────────────────────────────
PANSOU_NET="pansou_pansou-network"
if ! docker network inspect "$PANSOU_NET" &>/dev/null; then
  echo "警告: PanSou 网络 '$PANSOU_NET' 不存在"
  echo "请确认 PanSou 已部署，或修改 docker-compose.yml 中的网络名称"
  echo "可通过 'docker network ls | grep pansou' 查看实际网络名"
  read -r -p "是否继续部署？[y/N] " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
fi

ASTRBOT_NET="astrbot_astrbot_network"
docker network inspect "$ASTRBOT_NET" &>/dev/null || docker network create "$ASTRBOT_NET"

# ── 4. 创建数据持久化目录 ────────────────────────────────────────
mkdir -p data/postgresql data/redis

# ── 5. 构建并启动 ────────────────────────────────────────────────
echo "正在构建并启动 PRDS..."
docker compose up -d --build

# ── 6. 等待服务就绪 ──────────────────────────────────────────────
echo "等待服务启动..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8088/api/v1/health &>/dev/null; then
    break
  fi
  sleep 2
done

# ── 7. 验证部署 ──────────────────────────────────────────────────
echo ""
echo "=== 部署结果 ==="
if curl -sf http://localhost:8088/api/v1/health &>/dev/null; then
  SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
  echo "服务正常运行"
  echo "API 地址:    http://${SERVER_IP}:8088"
  echo "管理后台:    http://${SERVER_IP}:8088/admin"
  echo "查看日志:    docker logs -f prds"
else
  echo "服务可能未正常启动，请检查日志:"
  echo "  docker logs prds"
  exit 1
fi
