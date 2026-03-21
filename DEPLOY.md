# PRDS 部署指南

## 前置条件

- 服务器已安装 Docker 和 Docker Compose
- PanSou 服务已部署，且所在 Docker 网络为 `panso_pansou-network`

## 部署架构

PRDS 采用全合一单容器部署，一个容器内包含所有服务：

```
┌─────────────────────────────────┐
│         prds 容器 (:8088)        │
│                                 │
│  ┌───────────┐  ┌────────────┐  │
│  │ FastAPI   │  │  Worker    │  │
│  │ (API服务) │  │ (定时删除) │  │
│  └─────┬─────┘  └──────┬─────┘  │
│        │               │        │
│  ┌─────┴───────────────┴─────┐  │
│  │      PostgreSQL + Redis   │  │
│  └───────────────────────────┘  │
└──────────────┬──────────────────┘
               │ panso_pansou-network
        ┌──────┴──────┐
        │   PanSou    │
        │ (搜索服务)  │
        └─────────────┘
```

## 一、准备配置文件

```bash
# 克隆项目
git clone <repo-url> prds
cd prds

# 复制环境变量模板
cp .env.example .env
```

编辑 `.env`，必须修改的配置：

```bash
# 生产环境设置
APP_ENV=production
APP_DEBUG=false
APP_SECRET_KEY=<生成一个随机字符串>

# 数据库密码（生产环境必须更换）
POSTGRES_PASSWORD=<你的强密码>

# PanSou 地址（容器名:端口，通过 Docker 网络通信）
PANSOU_BASE_URL=http://pansou-api:8888

# 管理后台密码（生产环境必须更换）
ADMIN_PASSWORD=<你的管理密码>
```

可选调优配置：

```bash
# 搜索结果缓存时间（秒），默认 300 即 5 分钟
SEARCH_CACHE_TTL=300

# 已转存资源缓存时间（秒），默认 600 即 10 分钟
RESOURCE_CACHE_TTL=600

# 资源有效期（分钟），分享链接的存活时间
RESOURCE_TTL_MINUTES=10

# 资源删除延迟（分钟），创建后多久执行删除
DELETE_DELAY_MINUTES=20

# 默认返回最新的前N条资源（按时间降序），默认 5
DEFAULT_SEARCH_LIMIT=5

# 前端可请求的最大数量上限，默认 20
MAX_SEARCH_LIMIT=20
```

> `PANSOU_BASE_URL` 中的容器名和端口根据你的实际 PanSou 部署情况修改。可通过 `docker ps` 查看。

## 二、确认 PanSou 网络

```bash
# 确认 PanSou 网络存在
docker network ls | grep pansou

# 应该看到类似输出:
# xxxxxxxxxxxx   panso_pansou-network   bridge    local
```

如果网络名称不同，修改 `docker-compose.yml` 中的 `name` 字段：

```yaml
networks:
  pansou-network:
    external: true
    name: <你的实际网络名称>
```

## 三、构建并启动

```bash
# 构建镜像并启动（后台运行）
docker compose up -d --build

# 查看容器状态
docker ps

# 查看启动日志
docker logs -f prds
```

正常启动日志：

```
🚀 PRDS 全合一容器启动中...
📦 启动 PostgreSQL...
✅ PostgreSQL 17 就绪
📝 创建数据库用户: prds
📦 启动 Redis...
✅ Redis 就绪
📋 执行数据库迁移...
📝 首次部署，自动生成迁移文件...
✅ 数据库迁移完成
🔧 启动 Worker...
✅ Worker 启动 (PID: xxx)
🌐 启动 API 服务...
🚀 PRDS 启动 | 环境=production | 默认网盘=quark
```

## 四、验证部署

```bash
# 健康检查
curl http://localhost:8088/api/v1/health

# 测试搜索接口（默认夸克、最新5条）
curl -X POST http://localhost:8088/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"keyword":"测试"}'

# 完整参数测试（指定网盘类型和数量）
curl -X POST http://localhost:8088/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"keyword":"流浪地球", "pan_type":"quark", "limit":3}'
```

## 五、登录管理后台

浏览器访问 `http://<服务器IP>:8088/admin`，输入 `.env` 中设置的 `ADMIN_PASSWORD` 登录。

管理后台功能：
- **数据概览**: 搜索次数、资源数、转存/分享/删除成功数、活跃账号数
- **账号管理**: 新增/编辑/删除网盘账号
- **请求日志**: 查看最近的 API 调用记录

## 六、配置网盘账号

登录管理后台后，在「账号管理」页面点击「新增账号」。

也可通过 API 添加（需先登录获取 token）：

```bash
# 登录获取 token
TOKEN=$(curl -s -X POST http://localhost:8088/api/v1/admin/login \
  -H "Content-Type: application/json" \
  -d '{"password":"<你的管理密码>"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")

# 添加夸克网盘账号
curl -X POST http://localhost:8088/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $TOKEN" \
  -d '{
    "pan_type": "quark",
    "name": "夸克账号1",
    "cookie": "<你的夸克网盘Cookie>",
    "max_concurrency": 3,
    "save_folder_id": "0"
  }'
```

> Cookie 获取方式：登录夸克网盘网页版，从浏览器开发者工具中复制 Cookie。

## 七、数据库迁移

**首次部署时**，entrypoint.sh 会自动检测并生成迁移文件、执行建表，无需手动操作。

**后续部署时**，只有修改了数据库模型（`app/models/` 下的文件）才需要手动执行迁移：

```bash
docker exec -it prds bash -c "cd /app && alembic revision --autogenerate -m '描述变更' && alembic upgrade head"
```

如果只是修改业务逻辑、配置、前端页面等，直接 `docker compose up -d --build` 重新部署即可，不需要执行迁移。

## 八、常用运维命令

```bash
# 查看日志（实时跟踪）
docker logs -f prds

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 停止并清除数据卷（谨慎！会删除数据库数据）
docker compose down -v

# 重新构建（代码更新后）
docker compose up -d --build

# 进入容器排查问题
docker exec -it prds bash
```

## 九、数据备份

```bash
# 备份 PostgreSQL 数据
docker exec prds pg_dump -U prds prds > backup_$(date +%Y%m%d).sql

# 恢复数据
cat backup_20260321.sql | docker exec -i prds psql -U prds prds
```

## 十、端口说明

| 端口 | 用途 |
|------|------|
| 8088 (宿主机) | PRDS API + 管理后台对外端口 |
| 8000 (容器内) | FastAPI 服务端口 |
| 5432 (容器内) | PostgreSQL（仅容器内部访问） |
| 6379 (容器内) | Redis（仅容器内部访问） |

## 十一、故障排查

**容器无法启动**
```bash
docker logs prds
```

**无法连接 PanSou**
```bash
docker exec prds python -c "import httpx; print(httpx.get('http://pansou-api:8888/api/health').text)"
```

**数据库连接失败**
```bash
docker exec prds pg_isready -U prds
```

**管理后台无法登录**
- 检查 `.env` 中 `ADMIN_PASSWORD` 是否正确
- Token 每天自动轮换，隔天需重新登录

**转存失败**
- 管理后台检查账号 Cookie 有效性状态
- 检查账号空间是否充足
- 查看 `docker logs prds` 中的具体错误信息

## 十二、缓存说明

PRDS 实现了三层缓存以优化用户搜索体验：

| 层级 | 作用 | 默认 TTL |
|------|------|---------|
| L1 搜索缓存 | 相同关键词+类型+数量秒回 | 5 分钟 |
| L2 分布式锁 | 多人同时搜同一资源只转存一次 | 2 分钟 |
| L3 资源缓存 | 已转存的分享链接毫秒级返回 | 10 分钟 |

通过 `.env` 的 `SEARCH_CACHE_TTL` 和 `RESOURCE_CACHE_TTL` 调整缓存时间。
