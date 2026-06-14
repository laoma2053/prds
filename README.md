# PRDS - 网盘资源交付中台

Pan Resource Delivery Service

搜索网盘资源 -> 自动转存 -> 生成分享链接 -> 定时自动删除，为多个前端系统提供统一的资源交付 API。

## 核心能力

- **资源搜索**: 对接 PanSou 聚合搜索，支持指定网盘类型和返回数量
- **自动转存**: 通过账号池自动将资源转存到自有网盘
- **分享生成**: 自动生成带密码的分享链接
- **生命周期管理**: 资源 10 分钟有效，20 分钟后自动删除，降低网盘容量成本
- **链接有效性检测**: 转存前并行检测候选链接（最多 `limit × 5` 条，semaphore 限5并发），检测器按轮转分配避免单源过载，单次超时5s；检测到足够有效链接后立即取消剩余检测任务
- **流水线转存**: 有效链接边检测边转存，无需等所有检测完成再开始，大幅降低整体响应时间
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
  └─ L1 未命中 → 请求 PanSou → 取候选链接（最多 limit × 5 条）
        │
        ├─ 有账号（转存模式）
        │   ├─ [并行检测] semaphore(5) 并发，检测器轮转分配
        │   │    ├─ L3/DB 缓存命中 → 直接入转存队列（跳过检测）
        │   │    ├─ 链接失效 → 跳过
        │   │    └─ 链接有效 → 推入队列（凑够 limit 条立即取消剩余检测）
        │   └─ [流水线转存] 有效链接边检测边转存
        │        └─ 账号调度（LRU轮转）→ 转存 → 分享 → 写 DB + L3 缓存
        │
        └─ 无账号（直连模式）
             └─ [并行检测] 同上，有效链接直接返回原始 URL
```

### 三层缓存策略

| 层级 | Redis 键 | TTL | 作用 |
|------|---------|-----|------|
| L1 搜索缓存 | `prds:cache:search:{keyword}:{pan_type}:{limit}` | 5 分钟 | 相同关键词+类型+数量秒回 |
| L2 分布式锁 | `prds:lock:resource:{resource_key}` | 2 分钟 | 并发搜同一资源只转存一次 |
| L3 资源缓存 | `prds:cache:resource:{resource_key}` | 10 分钟 | 已转存的分享链接毫秒级返回 |

## 链接检测与账号调度

### 链接有效性检测

检测支持三个来源，按轮转分配（而非全部随机）以分散请求压力：

| 来源 | 类型 | 说明 |
|------|------|------|
| 自建 PanCheck | `PanCheckChecker` | 优先使用，需自行部署 |
| PanSou 内置 | `PanSouChecker` | 随 PanSou 服务可用 |
| 116818 第三方 | `ThirdPartyChecker` | 兜底，公共服务 |

检测结果：`True`（有效）/ `False`（失效，跳过）/ `None`（不确定，放行转存）

**并行检测策略**：

```
候选数 = min(PanSou结果总数, limit × 5)

semaphore(5)                     ← 同时最多5个检测并发
link_0 → checker_A               ← 按 index % 3 轮转分配
link_1 → checker_B
link_2 → checker_C
link_3 → checker_A（轮回）
...
↓
检测到 limit 个有效链接 → 取消剩余检测任务（不再消耗配额）
↓
有效链接流入转存流水线
```

单次检测超时：5s（并行模式下比串行更激进地淘汰死链）

**转存流水线**：检测有效的链接立即发起转存，无需等所有检测完成。检测和转存并发执行：

```
时刻1: link0,1,2,3,4 并发检测中
时刻2: link0 有效 → 立即开始转存 link0
时刻3: link2 有效 → 立即开始转存 link2（与 link0 转存并行）
       检测任务已凑够 limit=2，取消 link1,3,4 的检测
时刻4: link0,link2 转存完成 → 返回结果
```

---

### 账号池调度

**调度优先级**（三级排序）：

```
health_score DESC → weight DESC → last_used_at ASC（LRU）
```

| 维度 | 说明 |
|------|------|
| `health_score` | 健康评分（0-100），转存失败时降低，保证高质量账号优先 |
| `weight` | 手动设置的调度权重，高权重账号优先 |
| `last_used_at` | 最后使用时间，同分时优先选**最久未用**的账号 |

LRU 机制确保多账号均衡使用：账号 A 被用一次后，下次同等条件下账号 B 会被优先选中，实现自然轮转，避免单账号因高频使用触发网盘限频或封号风险。

`last_used_at` 存储于 Redis（键 `prds:pool:last_used:{account_id}`，TTL 1小时），账号 acquire 成功时写入。

## 支持的网盘

| 网盘 | Provider | 状态 |
|------|----------|------|
| 夸克网盘 | `QuarkProvider` | 已实现 |
| UC 网盘 | `UcProvider` | 已实现（继承夸克） |
| 百度网盘 | `BaiduProvider` | 已实现 |
| 迅雷网盘 | `XunleiProvider` | 规划中 |

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

- [心悦搜索](https://github.com/675061370/xinyue-search) - 网盘资源搜索前端
- [CloudSaver](https://github.com/jiangrui1994/cloudsaver) - 网盘资源转存工具
- [quark-auto-save](https://github.com/Cp0204/quark-auto-save) - 夸克网盘转存
- [PanCheck](https://github.com/Lampon/PanCheck) - 网盘链接有效性检测
- [pansou](https://github.com/fish2018/pansou) - 网盘资源聚合搜索
