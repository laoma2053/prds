# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**PRDS (Pan Resource Delivery Service)** - 网盘资源交付中台。

核心功能：接收前端请求 → 调用 PanSou 搜索资源 → 账号池调度 → 自动转存 → 生成分享链接 → 10分钟后自动删除资源。

完整设计文档位于 [docs/](docs/) 目录，阅读顺序见 [docs/00_项目总览与阅读顺序.md](docs/00_项目总览与阅读顺序.md)。

## 技术栈

- **后端**: FastAPI (Python) + PostgreSQL + Redis
- **异步任务**: Worker 队列架构（save/share/delete 三个队列）
- **容器化**: Docker Compose（api + worker + redis + postgres + pansou + nginx）

## 项目结构（待实现）

按 [docs/09_FastAPI项目结构.md](docs/09_FastAPI项目结构.md) 规范实现：

```
app/
├── core/          # 配置与依赖注入
├── api/           # 路由层（/api/v1）
├── schemas/       # 请求/响应 Pydantic 模型
├── models/        # SQLAlchemy 数据库模型
├── repositories/  # 数据访问层
├── providers/     # 网盘 Provider（Quark/Baidu/Aliyun）
├── services/      # 业务逻辑层
├── workers/       # 异步 Worker（save/share/delete）
└── utils/         # 工具函数
```

## 核心架构决策

### API 设计
- 基础路径 `/api/v1`，详见 [docs/02_API协议设计.md](docs/02_API协议设计.md)
- 统一返回结构：`{success, code, message, request_id, data}`
- 核心接口：`POST /api/v1/resources/search-and-deliver`（搜索并交付）、`GET /api/v1/tasks/{task_id}`（查询任务）

### Provider 抽象
所有网盘实现继承 `BaseProvider`，必须实现：`check_cookie()` / `save_share()` / `create_share()` / `delete_resource()`。详见 [docs/08_Provider抽象设计.md](docs/08_Provider抽象设计.md)。

### 账号池调度
过滤顺序：Cookie有效 → 空间充足 → 并发未超限。选择策略：health_score + weight + Round Robin。详见 [docs/06_账号池调度算法.md](docs/06_账号池调度算法.md)。

### 资源生命周期
- 资源创建后 TTL 10 分钟
- 20 分钟后执行实际删除
- 用 Redis ZSET (`prds:delete_due`) 管理待删除队列
- 详见 [docs/05_资源生命周期设计.md](docs/05_资源生命周期设计.md)

### 条件执行逻辑
仅当系统中配置了对应网盘类型的有效账号时，才执行转存/分享/删除流程；否则直接返回 PanSou 原始链接。

## Redis 键命名规范

```
prds:cache:search:{keyword}        # 搜索缓存
prds:cache:resource:{resource_key} # 资源缓存
prds:lock:resource:{resource_key}  # 分布式锁
prds:rate:client:{client}          # 客户端限流
prds:pool:concurrency:{account}    # 账号并发控制
prds:delete_due                    # 待删除 ZSET（score=到期时间戳）
```

## 开发阶段规划

按 [docs/12_开发阶段与AI提示词.md](docs/12_开发阶段与AI提示词.md) 中的提示词顺序实现：
1. FastAPI 骨架 → 2. 数据库 → 3. PanSou 集成 → 4. Provider → 5. ResourceService → 6. Worker → 7. Docker

## 参考实现

- 夸克网盘操作参考：https://github.com/Cp0204/quark-auto-save
- 网盘有效性检测：https://github.com/Lampon/PanCheck
- PanSou 搜索服务：https://github.com/fish2018/pansou
- **quark-save**: https://github.com/henggedaren/quark-save
