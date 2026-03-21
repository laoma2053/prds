# PRDS API 接入文档

面向前端项目的 API 调用指南。

## 基础信息

| 项目 | 值 |
|------|-----|
| Base URL | `http://<服务器IP>:8088` |
| 协议 | HTTP JSON |
| Content-Type | `application/json` |
| 字符编码 | UTF-8 |

## 统一返回结构

所有接口返回相同 JSON 结构：

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "request_id": "a1b2c3d4e5f6...",
  "data": { ... }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | bool | 请求是否成功 |
| `code` | string | 状态码，成功为 `"OK"`，失败为具体错误码 |
| `message` | string | 描述信息 |
| `request_id` | string | 请求唯一ID，排查问题时提供给后端 |
| `data` | any | 业务数据，失败时可能为 null |

**判断请求是否成功，只看 `success` 字段，不要看 HTTP 状态码。**

---

## 接口列表

### 1. 健康检查

检测 PRDS 服务是否正常运行。建议前端项目在初始化时调用一次。

```
GET /api/v1/health
```

**返回示例：**

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "request_id": "...",
  "data": {
    "status": "healthy"
  }
}
```

---

### 2. 搜索资源（核心接口）

搜索网盘资源并返回分享链接。这是前端项目最常调用的接口。

```
POST /api/v1/search
Content-Type: application/json
```

**请求参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `keyword` | string | 是 | - | 搜索关键词，1-200 字符 |
| `pan_type` | string | 否 | `"quark"` | 指定网盘类型：`quark` / `baidu` / `aliyun` / `115` / `xunlei` 等 |
| `limit` | int | 否 | `5` | 返回资源数量（1-20），按时间降序取最新的前 N 条 |
| `client_id` | string | 否 | `"default"` | 调用方标识，用于后台统计区分 |

**请求示例：**

最简请求（只传关键词，其他用默认值）：
```json
{
  "keyword": "流浪地球"
}
```

完整请求（指定网盘类型 + 数量）：
```json
{
  "keyword": "流浪地球",
  "pan_type": "quark",
  "limit": 3,
  "client_id": "my-website"
}
```

只要百度网盘资源，取最新 10 条：
```json
{
  "keyword": "流浪地球",
  "pan_type": "baidu",
  "limit": 10,
  "client_id": "my-bot"
}
```

**成功返回：**

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "request_id": "...",
  "data": {
    "mode": "proxy",
    "results": [
      {
        "title": "流浪地球(两部合集)4K.HDR",
        "pan_type": "quark",
        "url": "https://pan.quark.cn/s/xxxxxx",
        "password": null,
        "mode": "proxy",
        "expire_at": "2026-03-21T10:30:00+00:00"
      },
      {
        "title": "流浪地球2 4K",
        "pan_type": "quark",
        "url": "https://pan.quark.cn/s/yyyyyy",
        "password": "",
        "mode": "proxy",
        "expire_at": "2026-03-21T10:30:00+00:00"
      },
      {
        "title": "流浪地球 1-2合集 1080P",
        "pan_type": "quark",
        "url": "https://pan.quark.cn/s/zzzzzz",
        "password": "",
        "mode": "proxy",
        "expire_at": "2026-03-21T10:30:00+00:00"
      }
    ]
  }
}
```

**返回字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `data.mode` | string | 整体模式：`proxy`（经过转存）或 `direct`（原始链接） |
| `data.results` | array | 资源列表，按发布时间降序排列，数量由 `limit` 控制 |
| `results[].title` | string | 资源标题 |
| `results[].pan_type` | string | 网盘类型 |
| `results[].url` | string | 分享链接（proxy 模式下为 PRDS 生成的临时链接） |
| `results[].password` | string/null | 分享密码，可能为空 |
| `results[].mode` | string | 该条结果的模式：`proxy` 或 `direct` |
| `results[].expire_at` | string/null | 过期时间（ISO 8601），仅 proxy 模式有值 |

**失败返回：**

```json
{
  "success": false,
  "code": "SEARCH_ERROR",
  "message": "具体错误信息",
  "request_id": "...",
  "data": null
}
```

**无结果返回：**

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "request_id": "...",
  "data": {
    "mode": "direct",
    "results": []
  }
}
```

---

## 两种返回模式

### proxy 模式（转存模式）

当 PRDS 后台配置了对应网盘类型的账号时，会自动执行：搜索 -> 按时间取最新N条 -> 转存到 PRDS 账号 -> 生成临时分享链接 -> 返回。

**特点：**
- `url` 是 PRDS 生成的临时链接
- `expire_at` 标识链接有效期（默认 10 分钟）
- 前端**必须提示用户尽快转存**，过期后链接失效
- 同一资源短时间内重复搜索会命中缓存，秒级返回

### direct 模式（直连模式）

当 PRDS 未配置对应网盘类型的账号时，直接返回 PanSou 搜索到的原始链接。

**特点：**
- `url` 是第三方原始分享链接
- `expire_at` 为 null
- 链接有效性取决于原始分享者，PRDS 不保证
- 无需提示过期时间

---

## 前端接入示例

### JavaScript / TypeScript

```javascript
const PRDS_URL = 'http://your-server:8088';

async function searchResource(keyword, panType = null, limit = null) {
  const body = { keyword, client_id: 'my-app' };
  if (panType) body.pan_type = panType;
  if (limit) body.limit = limit;

  const res = await fetch(`${PRDS_URL}/api/v1/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  const json = await res.json();

  if (!json.success) {
    throw new Error(json.message);
  }

  return json.data;
}

// 示例: 搜索夸克网盘最新3条
const data = await searchResource('流浪地球', 'quark', 3);

if (data.results.length === 0) {
  console.log('未找到资源');
} else {
  for (const item of data.results) {
    console.log(`${item.title} - ${item.url}`);
    if (item.mode === 'proxy' && item.expire_at) {
      console.log(`临时链接，${item.expire_at} 后过期`);
    }
  }
}
```

### Python

```python
import httpx

PRDS_URL = "http://your-server:8088"

async def search_resource(keyword: str, pan_type: str = None, limit: int = None) -> dict:
    body = {"keyword": keyword, "client_id": "my-bot"}
    if pan_type:
        body["pan_type"] = pan_type
    if limit:
        body["limit"] = limit

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{PRDS_URL}/api/v1/search", json=body)
        data = resp.json()

    if not data["success"]:
        raise Exception(data["message"])

    return data["data"]

# 示例: 搜索百度网盘最新5条
data = await search_resource("流浪地球", pan_type="baidu", limit=5)
```

### cURL

```bash
# 最简调用（默认夸克、默认5条）
curl -X POST http://your-server:8088/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"keyword": "流浪地球"}'

# 完整调用（指定网盘类型和数量）
curl -X POST http://your-server:8088/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"keyword": "流浪地球", "pan_type": "quark", "limit": 3, "client_id": "test"}'
```

---

## 注意事项

### 超时设置

搜索接口涉及外部服务调用（PanSou 搜索 + 网盘转存），耗时较长：

| 场景 | 预期耗时 | 建议超时 |
|------|---------|---------|
| 缓存命中 | < 100ms | - |
| 仅搜索（direct 模式） | 1-5 秒 | 15 秒 |
| 搜索 + 转存（proxy 模式） | 10-30 秒 | 60 秒 |

**前端 HTTP 客户端超时建议设为 60 秒。** `limit` 越大转存耗时越长。

### 缓存机制

- 相同 `keyword` + `pan_type` + `limit` 在 5 分钟内重复请求会命中缓存，毫秒级返回
- 已转存的资源在 10 分钟内被其他关键词搜到也会复用，不重复转存
- 多个用户同时搜索同一资源，只会触发一次转存，其他请求等待结果

### 频率限制

- 建议前端做防抖，避免用户快速重复提交
- `client_id` 用于后台统计各调用方的请求量，建议每个前端项目使用唯一标识

### 错误处理建议

```javascript
try {
  const data = await searchResource(keyword, panType, limit);
  if (data.results.length === 0) {
    showMessage('未找到相关资源，请换个关键词试试');
  } else {
    renderResults(data.results);
  }
} catch (err) {
  showMessage('搜索服务暂时不可用，请稍后重试');
}
```

### proxy 模式前端展示建议

当 `results[].mode === "proxy"` 时：

1. 展示资源标题和分享链接
2. 如果有 `password`，一起展示
3. **显著提示**：「临时链接，请在 XX 分钟内转存到自己的网盘」
4. 可根据 `expire_at` 做倒计时展示
5. 过期后该链接将失效，用户需重新搜索

### 跨域（CORS）

当前 PRDS 未配置 CORS。如果前端是浏览器直接调用（而非后端转发），需要在 PRDS 添加 CORS 中间件。请联系 PRDS 管理员配置，或通过前端项目的后端做代理转发。

---

## 错误码参考

| code | 说明 |
|------|------|
| `OK` | 成功 |
| `SEARCH_ERROR` | 搜索服务异常 |
| `BAD_REQUEST` | 请求参数错误（如 limit 超出 1-20 范围） |
| `INTERNAL_ERROR` | 服务内部错误 |

---

## 变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.2.0 | 2026-03-21 | 新增 `limit` 参数，资源按时间降序返回最新N条 |
| v0.1.0 | 2026-03-21 | 初始版本，支持搜索接口 + 夸克网盘转存 |
