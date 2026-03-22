# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**PRDS (Pan Resource Delivery Service)** - 网盘资源交付中台。

核心功能：接收前端请求 → 调用 PanSou 搜索资源 → 账号池调度 → 自动转存 → 生成分享链接 → 10分钟后自动删除资源。

完整设计文档位于 [docs/](docs/) 目录，阅读顺序见 [docs/00_项目总览与阅读顺序.md](docs/00_项目总览与阅读顺序.md)。

## 技术栈

- **后端**: FastAPI (Python 3.11+) + PostgreSQL 16 + Redis 7
- **ORM**: SQLAlchemy 2.0 (async) + Alembic
- **HTTP 客户端**: httpx (异步)
- **异步任务**: Worker 定时删除过期资源
- **容器化**: Docker 全合一单容器（PostgreSQL + Redis + API + Worker）

## 开发命令

### 本地开发（需先启动 PostgreSQL + Redis）

```bash
# 安装依赖
pip install -e .

# 安装开发依赖
pip install -e ".[dev]"

# 启动 API 服务（开发模式，自动重载）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 启动 Worker（定时删除任务）
python -m app.workers.main

# 代码检查
ruff check .

# 代码格式化
ruff format .

# 运行测试
pytest

# 运行单个测试文件
pytest tests/test_resource_service.py

# 运行单个测试函数
pytest tests/test_resource_service.py::test_search_and_deliver
```

### 数据库迁移

```bash
# 生成迁移文件（修改 models 后）
alembic revision --autogenerate -m "描述变更"

# 执行迁移
alembic upgrade head

# 回滚一个版本
alembic downgrade -1

# 查看迁移历史
alembic history
```

### Docker 部署

```bash
# 构建并启动（后台运行）
docker compose up -d --build

# 查看日志
docker logs -f prds

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 进入容器
docker exec -it prds bash

# 容器内执行迁移
docker exec -it prds bash -c "cd /app && alembic upgrade head"
```

### 测试接口

```bash
# 健康检查
curl http://localhost:8088/api/v1/health

# 搜索资源（默认夸克、最新5条）
curl -X POST http://localhost:8088/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"keyword":"测试"}'

# 完整参数搜索
curl -X POST http://localhost:8088/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"keyword":"流浪地球", "pan_type":"quark", "limit":3}'
```

## 项目结构

```
app/
├── main.py                 # FastAPI 应用入口 + /admin 路由
├── core/                   # 配置、数据库、Redis、异常处理
│   ├── config.py           # 环境变量配置（Pydantic Settings）
│   ├── database.py         # PostgreSQL 连接池（async）
│   ├── redis.py            # Redis 连接池
│   └── exceptions.py       # 自定义异常类
├── api/v1/                 # 路由层（/api/v1）
│   ├── health.py           # GET /api/v1/health
│   ├── resources.py        # POST /api/v1/search（核心搜索接口）
│   └── admin.py            # 管理后台 API（登录鉴权 + 账号 CRUD + 统计）
├── schemas/                # Pydantic 请求/响应模型
│   ├── resource.py         # 搜索请求/响应
│   └── response.py         # 统一返回结构
├── models/                 # SQLAlchemy 数据库模型
│   ├── base.py             # Base 类
│   ├── pan_account.py      # 网盘账号表
│   ├── resource.py         # 资源表
│   └── task.py             # 任务表
├── repositories/           # 数据访问层（Repository 模式）
│   ├── pan_account.py      # 账号池查询与调度
│   ├── resource.py         # 资源 CRUD
│   └── task.py             # 任务 CRUD
├── providers/              # 网盘 Provider 抽象
│   ├── base.py             # BaseProvider 抽象类
│   └── quark.py            # QuarkProvider 实现
├── services/               # 业务逻辑层
│   ├── pansou_client.py    # PanSou HTTP 客户端
│   ├── resource_service.py # 核心业务逻辑（搜索+转存+分享）
│   └── scheduler.py        # 账号池调度算法
└── workers/                # 异步 Worker
    ├── main.py             # Worker 启动入口
    └── delete_worker.py    # 定时删除过期资源
static/
└── admin.html              # 管理后台 Web 页面（Tailwind + Alpine.js）
migrations/                 # Alembic 数据库迁移
```

## 核心架构决策

### 三层缓存策略
- **L1 搜索缓存**: `prds:cache:search:{keyword}:{pan_type}:{limit}` (5分钟) - 相同搜索秒回
- **L2 分布式锁**: `prds:lock:resource:{resource_key}` (2分钟) - 并发搜索同一资源只转存一次
- **L3 资源缓存**: `prds:cache:resource:{resource_key}` (10分钟) - 已转存分享链接毫秒级返回

### API 设计
- 基础路径 `/api/v1`，详见 [docs/02_API协议设计.md](docs/02_API协议设计.md)
- 统一返回结构：`{success, code, message, request_id, data}`
- 核心接口：`POST /api/v1/search`（搜索并交付）

### Provider 抽象
所有网盘实现继承 `BaseProvider`，必须实现：
- `check_cookie()` - 检查 Cookie 有效性
- `save_share()` - 转存分享链接到自己网盘
- `create_share()` - 创建分享链接
- `delete_resource()` - 删除资源

当前支持：夸克网盘（`QuarkProvider`）。扩展新网盘只需继承 `BaseProvider` 并在 `app/providers/__init__.py` 注册。详见 [docs/08_Provider抽象设计.md](docs/08_Provider抽象设计.md)。

### 账号池调度
过滤顺序：Cookie有效 → 空间充足 → 并发未超限。选择策略：health_score + weight + Round Robin。详见 [docs/06_账号池调度算法.md](docs/06_账号池调度算法.md)。

### 资源生命周期
- 资源创建后 TTL 10 分钟（分享链接有效期）
- 20 分钟后执行实际删除（给予缓冲时间）
- 用 Redis ZSET (`prds:delete_due`) 管理待删除队列，Worker 定时扫描
- 详见 [docs/05_资源生命周期设计.md](docs/05_资源生命周期设计.md)

### 条件执行逻辑
仅当系统中配置了对应网盘类型的有效账号时，才执行转存/分享/删除流程；否则直接返回 PanSou 原始链接（智能降级）。

## Redis 键命名规范

```
prds:cache:search:{keyword}:{pan_type}:{limit}  # L1 搜索缓存
prds:cache:resource:{resource_key}              # L3 资源缓存
prds:lock:resource:{resource_key}               # L2 分布式锁
prds:rate:client:{client}                       # 客户端限流
prds:pool:concurrency:{account_id}              # 账号并发控制
prds:delete_due                                 # 待删除 ZSET（score=到期时间戳）
```

## 核心业务流程

用户搜索 → L1 缓存命中直接返回 → 未命中调用 PanSou → 遍历结果检查 L3 缓存 → 未命中获取 L2 锁 → 账号池调度 → 转存 → 分享 → 写入 DB + L3 缓存 → 返回结果。

无对应网盘账号时直接返回 PanSou 原始链接（智能降级）。

## 重要约束

### 修改 Provider 时
- 必须保持 `BaseProvider` 接口签名不变
- 新增网盘类型需在 `app/providers/__init__.py` 注册
- Cookie 验证失败时必须更新 `pan_account.is_valid = False`

### 修改数据库模型时
- 必须生成 Alembic 迁移文件：`alembic revision --autogenerate -m "描述"`
- 执行迁移：`alembic upgrade head`
- Docker 部署时首次启动会自动执行迁移

### 修改 Redis 键时
- 必须同步更新 [docs/04_Redis缓存设计.md](docs/04_Redis缓存设计.md)
- 键名必须带 `prds:` 前缀避免冲突

### 环境变量
- 生产环境必须修改：`APP_SECRET_KEY`、`POSTGRES_PASSWORD`、`ADMIN_PASSWORD`
- 所有配置通过 `.env` 文件管理，不要硬编码

## 参考实现

- 夸克网盘操作：https://github.com/Cp0204/quark-auto-save
- 夸克转存+分享：https://github.com/henggedaren/quark-save
- 网盘有效性检测：https://github.com/Lampon/PanCheck
- PanSou 搜索服务：https://github.com/fish2018/pansou
