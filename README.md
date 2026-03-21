# PRDS - 网盘资源交付中台

Pan Resource Delivery Service

搜索网盘资源 -> 自动转存 -> 生成分享链接 -> 定时自动删除，为多个前端系统提供统一的资源交付 API。

## 核心能力

- **资源搜索**: 对接 PanSou 聚合搜索，支持指定网盘类型和返回数量
- **自动转存**: 通过账号池自动将资源转存到自有网盘
- **分享生成**: 自动生成带密码的分享链接
- **生命周期管理**: 资源 10 分钟有效，20 分钟后自动删除，降低网盘容量成本
- **智能降级**: 未配置对应网盘账号时，直接返回原始链接
- **三层缓存**: L1 搜索缓存 + L2 分布式锁防重复转存 + L3 已转存资源缓存，重复搜索毫秒级响应
- **账号池调度**: 多账号并发管理，健康评分 + 权重 + 轮询策略
- **管理后台**: Web 界面管理账号 + 查看 API 调用数据统计（密码登录鉴权）

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端框架 | FastAPI (Python 3.11+) |
| 数据库 | PostgreSQL 16 |
| 缓存/队列 | Redis 7 |
| HTTP 客户端 | httpx (异步) |
| ORM | SQLAlchemy 2.0 (async) |
| 数据库迁移 | Alembic |
| 管理后台前端 | Tailwind CSS + Alpine.js (单 HTML 文件) |
| 容器化 | Docker (全合一单容器) |

## 项目结构

```
app/
├── main.py                 # FastAPI 应用入口 + /admin 页面路由
├── core/                   # 配置、数据库、Redis、异常处理
├── api/v1/
│   ├── health.py           # GET  /api/v1/health
│   ├── resources.py        # POST /api/v1/search（核心搜索接口）
│   └── admin.py            # 管理后台 API（登录鉴权 + 账号 CRUD + 数据统计）
├── schemas/                # Pydantic 请求/响应模型
├── models/                 # SQLAlchemy 数据库模型
├── repositories/           # 数据访问层
├── providers/              # 网盘 Provider（当前支持夸克）
├── services/               # 业务逻辑层
└── workers/                # 异步 Worker（定时删除过期资源）
static/
└── admin.html              # 管理后台 Web 页面
```

## API 接口

### 公开接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/search` | 搜索并交付资源（核心接口） |
| GET | `/api/v1/health` | 健康检查 |

### 管理接口（需登录）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/admin/login` | 登录（无需鉴权） |
| GET | `/api/v1/admin/accounts` | 获取网盘账号列表 |
| POST | `/api/v1/admin/accounts` | 新增网盘账号 |
| PUT | `/api/v1/admin/accounts/{id}` | 更新网盘账号 |
| DELETE | `/api/v1/admin/accounts/{id}` | 删除网盘账号 |
| GET | `/api/v1/admin/stats` | 数据统计 |
| GET | `/api/v1/admin/stats/recent` | 最近请求日志 |

管理接口需在请求头携带 `X-Admin-Token`，通过 `/api/v1/admin/login` 获取。

### 管理后台 Web 页面

访问 `http://<host>:8088/admin` 打开管理后台，输入密码登录。密码在 `.env` 的 `ADMIN_PASSWORD` 中配置。

### 搜索请求示例

```json
POST /api/v1/search
{
  "keyword": "流浪地球",
  "pan_type": "quark",
  "limit": 3,
  "client_id": "my-app"
}
```

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `keyword` | 是 | - | 搜索关键词 |
| `pan_type` | 否 | `quark` | 网盘类型 |
| `limit` | 否 | `5` | 返回数量（1-20），按时间降序取最新 |
| `client_id` | 否 | `default` | 调用方标识 |

详细接口文档见 [API.md](API.md)。

### 统一返回结构

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "request_id": "...",
  "data": {}
}
```

## 核心流程

```
用户搜索 "流浪地球" (pan_type=quark, limit=3)
  │
  ├─ L1 搜索缓存命中 → 直接返回（毫秒级）
  │
  ├─ L1 未命中 → 请求 PanSou → 按时间降序取最新3条
  │     │
  │     ├─ L3 资源缓存命中 → 直接返回（毫秒级）
  │     │
  │     ├─ DB 有可用实例 → 返回并写入 L3 缓存
  │     │
  │     └─ 均未命中 → 获取 L2 分布式锁
  │           ├─ 拿到锁 → 账号调度 → 转存 → 分享 → 写 DB + L3 缓存
  │           └─ 未拿到 → 等待结果（最多30秒）→ 超时降级返回原始链接
  │
  └─ 无对应账号 → 直接返回 PanSou 原始链接（最新3条）
```

### 三层缓存策略

| 层级 | Redis 键 | TTL | 作用 |
|------|---------|-----|------|
| L1 搜索缓存 | `prds:cache:search:{keyword}:{pan_type}:{limit}` | 5 分钟 | 相同关键词+类型+数量秒回 |
| L2 分布式锁 | `prds:lock:resource:{resource_key}` | 2 分钟 | 并发搜同一资源只转存一次 |
| L3 资源缓存 | `prds:cache:resource:{resource_key}` | 10 分钟 | 已转存的分享链接毫秒级返回 |

## 支持的网盘

| 网盘 | Provider | 状态 |
|------|----------|------|
| 夸克网盘 | `QuarkProvider` | 已实现 |
| 百度网盘 | `BaiduProvider` | 待实现 |
| 阿里云盘 | `AliyunProvider` | 待实现 |

扩展新网盘只需：继承 `BaseProvider` 实现四个方法，在 `app/providers/__init__.py` 注册即可。

## 性能配置（16核16G 服务器参考）

| 组件 | 配置 | 说明 |
|------|------|------|
| uvicorn workers | 8 进程 | 匹配 CPU 核数 |
| PG 连接池 | pool=40, overflow=20 | 支撑 60 并发 DB 操作 |
| Redis 连接池 | max=200 | 支撑高并发缓存读写 |
| Redis 内存 | 1GB | 存储搜索缓存 + 资源缓存 + 锁 |

搜索接口性能预估：
- 缓存命中: ~2000 QPS
- 直连模式（仅搜索）: ~300-500 QPS（受 PanSou 限制）
- 转存模式: N 个账号 x 3 并发（受网盘 API 限制）

## 快速开始

详见 [DEPLOY.md](DEPLOY.md)。

## 设计文档

详细架构设计位于 `docs/` 目录：

| 文档 | 内容 |
|------|------|
| 00_项目总览与阅读顺序 | 项目概述与文档索引 |
| 01_PRD_产品需求文档 | 产品需求与核心流程 |
| 02_API协议设计 | 接口规范与返回结构 |
| 03_数据库表结构设计 | 6 张核心表设计 |
| 04_Redis缓存设计 | 缓存/锁/限流/队列键设计 |
| 05_资源生命周期设计 | TTL 与删除策略 |
| 06_账号池调度算法 | 过滤与选择策略 |
| 07_Worker任务架构 | 异步任务队列设计 |
| 08_Provider抽象设计 | 网盘接口抽象 |
| 09_FastAPI项目结构 | 代码分层规范 |
| 10_Docker部署架构 | 容器化方案 |
| 11_高并发架构设计 | 并发控制策略 |
| 12_开发阶段与AI提示词 | 开发顺序规划 |
| 13_上线检查清单 | 生产环境检查项 |

## 参考项目

- [quark-auto-save](https://github.com/Cp0204/quark-auto-save) - 夸克网盘转存
- [quark-save](https://github.com/henggedaren/quark-save) - 夸克网盘转存+分享
- [PanCheck](https://github.com/Lampon/PanCheck) - 网盘链接有效性检测
- [pansou](https://github.com/fish2018/pansou) - 网盘资源聚合搜索
