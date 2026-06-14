#!/bin/bash
#使用方式：chmod +x update.sh && ./update.sh
set -e

# 拉取最新代码（非 git 环境跳过）
git pull 2>/dev/null || echo "非 git 仓库，跳过 git pull，请手动上传最新文件"

# 重新构建并启动
docker compose up -d --build

# 等待服务就绪
echo "等待服务启动..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8088/api/v1/health &>/dev/null; then
    break
  fi
  sleep 2
done

if curl -sf http://localhost:8088/api/v1/health &>/dev/null; then
  SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
  ADMIN_PWD=$(grep "^ADMIN_PASSWORD=" .env 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || echo "见 .env")
  echo "更新完成，服务运行正常"
  echo "管理后台:    http://${SERVER_IP}:8088/admin"
  echo "管理密码:    ${ADMIN_PWD}"
  echo "查看日志:    docker logs -f prds"
else
  echo "服务可能未正常启动，请检查日志: docker logs prds"
  exit 1
fi
