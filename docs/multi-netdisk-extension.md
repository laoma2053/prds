# 多网盘扩展开发规划

## 目标

在现有夸克网盘基础上，扩展支持 **UC网盘、百度网盘、迅雷网盘** 的搜索、转存、分享功能。

参考实现来源：https://github.com/675061370/xinyue-search/tree/main/extend/netdisk/pan

---

## 三种网盘技术特征

| 特征 | 夸克（已有） | UC | 百度 | 迅雷 |
|------|------------|-----|------|------|
| 认证方式 | Cookie | Cookie | Cookie + bdstoken | OAuth2 refresh_token |
| API base URL | `https://drive-h.quark.cn` | `https://pc-api.uc.cn` | `https://pan.baidu.com` | `https://api-pan.xunlei.com` |
| 请求参数标识 | `pr=ucpro&fr=pc` | `pr=UCBrowser&fr=pc` | 独立参数体系 | 独立参数体系 |
| 转存目录参数 | folder fid | folder fid | 路径字符串 | folder id |
| 分享密码 | 无 | 无 | 有（固定 `6666`） | 有（pass_code） |
| 分享链接格式 | `https://pan.quark.cn/s/xxx` | `https://drive.uc.cn/s/xxx` | `https://pan.baidu.com/s/xxx?pwd=6666` | `https://pan.xunlei.com/s/xxx?pwd=xxxx` |
| 实现复杂度 | 基准 | 低（继承夸克） | 中 | 高（token刷新） |

---

## 项目架构（现状）

```
app/providers/
├── base.py       ← BaseProvider 抽象类（已实现）
├── quark.py      ← QuarkProvider（已实现）
└── __init__.py   ← _REGISTRY 注册表 + get_provider() 工厂（已实现）
```

**Phase 1 已完成**：`BaseProvider` 接口、工厂函数 `get_provider(pan_type)` 均已就位，无需改动。

---

## BaseProvider 接口（base.py，已有）

```python
class BaseProvider(ABC):
    pan_type: str = ""

    async def check_cookie(self, cookie: str) -> bool: ...
    async def save_share(self, share_url: str, cookie: str, save_folder_id: str = "0") -> SaveResult: ...
    async def create_share(self, file_id: str, file_name: str, cookie: str) -> ShareResult: ...
    async def delete_resource(self, file_id: str, cookie: str) -> DeleteResult: ...
```

新增网盘只需继承 `BaseProvider`，实现四个方法，然后在 `__init__.py` 的 `_REGISTRY` 注册。

---

## 数据库字段说明

`PanAccount` 模型的关键字段在不同平台含义不同：

| platform | `cookie` 字段存储内容 | `save_folder_id` 字段含义 |
|----------|----------------------|--------------------------|
| quark | 夸克完整 Cookie | 目录 fid（字符串） |
| uc | UC完整 Cookie | 目录 fid（字符串） |
| baidu | 百度完整 Cookie | 目录路径（如 `/来自搜索站`） |
| xunlei | 迅雷 refresh_token | 目录 id（数字字符串） |

---

## 分阶段开发计划

### Phase 2 — UC 网盘

**复杂度**：低。

**实现方式**：`UcProvider(QuarkProvider)` 继承夸克，覆盖三个模块级常量：

```python
# app/providers/uc.py
from app.providers.quark import QuarkProvider

BASE_URL = "https://pc-api.uc.cn"
PAN_URL = "https://drive.uc.cn"

class UcProvider(QuarkProvider):
    pan_type = "uc"
    # 覆盖 _build_headers 中的 origin/referer，以及 _COMMON_PARAMS 的 pr 参数
```

关键差异（相对夸克）：
- `BASE_URL`: `drive-h.quark.cn` → `pc-api.uc.cn`
- `PAN_URL`: `pan.quark.cn` → `drive.uc.cn`
- `pr` 参数: `ucpro` → `UCBrowser`
- `Referer`: `pan.quark.cn/` → `drive.uc.cn/`

**注册**：
```python
# app/providers/__init__.py
from app.providers.uc import UcProvider
_REGISTRY = {
    "quark": QuarkProvider(),
    "uc": UcProvider(),
}
```

**验证**：UC 转存功能正常，夸克无回归。

---

### Phase 3 — 百度网盘

**复杂度**：中。独立实现 `app/providers/baidu.py`。

**转存流程**（参考 BaiduPan.php）：
1. `GET /api/gettemplatevariable` → 提取 `bdstoken`（从响应 JSON）
2. 如有提取码，`POST /api/shorturlinfo` 获取 `randsk`，更新 Cookie 中的 `BDCLND`
3. `GET /share/wxlist` → 获取 `shareId`、`userId`、`fsIds`（文件 ID 列表）
4. 检查/创建目标目录（`save_folder_id` 为路径字符串，非 fid）
5. `POST /share/transfer` → 执行转存，返回转存后的文件路径
6. `POST /share/set` → 创建分享，固定密码 `6666`
7. 返回 `ShareResult(share_url=f"...?pwd=6666", share_password="6666")`

**关键 API 端点**：
```
GET  https://pan.baidu.com/api/gettemplatevariable  → bdstoken
POST https://pan.baidu.com/api/shorturlinfo          → 验证提取码
GET  https://pan.baidu.com/share/wxlist              → 分享文件列表
POST https://pan.baidu.com/share/transfer            → 转存
POST https://pan.baidu.com/share/set                 → 创建分享
POST https://pan.baidu.com/api/filemanager           → 删除（opera=delete）
```

**`delete_resource` 特殊处理**：
- 百度删除接口参数为路径数组：`filelist=["/path/to/file"]`
- `file_id` 字段存**文件路径**（非 fid），调用方在写入 DB 时须注意

**注意**：百度对转存频率限制较严，需在 `health_score` 机制中增加失败计数降权。

---

### Phase 4 — 迅雷网盘

**复杂度**：高。需 OAuth2 token 刷新 + 异步轮询。独立实现 `app/providers/xunlei.py`。

**认证机制**：
- `PanAccount.cookie` 存 `refresh_token`（非 Cookie 字符串）
- `access_token` 通过 `refresh_token` 换取，TTL ~2小时，缓存 Redis
- `captcha_token` 独立获取，TTL ~1小时，缓存 Redis
- Redis key：`prds:xunlei:access_token:{account_id}`、`prds:xunlei:captcha_token:{account_id}`
- 固定常量：`CLIENT_ID = 'Xqp0kJBXWhwaTpB6'`、`DEVICE_ID = '925b7631473a13716b791d7f28289cad'`

**Token 刷新流程**：
```
POST https://xluser-ssl.xunlei.com/v1/auth/token
body: { client_id, grant_type: 'refresh_token', refresh_token }
→ 返回新 access_token + 新 refresh_token
→ 新 refresh_token 写回 PanAccount.cookie（需更新 DB）
→ access_token 写入 Redis，TTL = expires_in - 60
```

**转存流程**：
1. 获取 `access_token` + `captcha_token`（优先读 Redis）
2. `GET /drive/v1/share` → 分享信息（`pass_code_token`、文件列表）
3. `POST /drive/v1/share/restore` → 转存到 `save_folder_id`（parent_id）
4. `GET /drive/v1/tasks/{task_id}` → 轮询直到 `progress == 100`
5. `POST /drive/v1/share` → 创建分享，返回 `share_url + pass_code`
6. 返回 `ShareResult(share_url=f"{share_url}?pwd={pass_code}", share_password=pass_code)`

**`delete_resource` 特殊处理**：
- 删除前须先刷新 token
- `POST /drive/v1/files:batchDelete`，body: `{"ids": [file_id], "space": ""}`

---

### Phase 5 — 联动更新

> **说明**：Phase 5 不是一次性完成的，每完成一个 Phase 就同步执行对应的联动变更，即可独立部署上线。

**`app/providers/__init__.py`**（随每个 Phase 递增注册）：
```python
# Phase 2 完成后
_REGISTRY = {"quark": QuarkProvider(), "uc": UcProvider()}

# Phase 3 完成后
_REGISTRY = {"quark": QuarkProvider(), "uc": UcProvider(), "baidu": BaiduProvider()}

# Phase 4 完成后（最终状态）
_REGISTRY = {"quark": QuarkProvider(), "uc": UcProvider(), "baidu": BaiduProvider(), "xunlei": XunleiProvider()}
```

**`app/workers/delete_worker.py`** 和 **`app/services/resource_service.py`**：
- 均已通过 `get_provider(pan_type)` 工厂解耦，无需修改

**`static/admin.html`** — 账号管理弹窗需修改三处：

**① 网盘类型 select 选项**（去掉 `aliyun`，按 Phase 进度追加）：
```html
<select x-model="form.pan_type" ...>
  <option value="quark">夸克网盘</option>
  <option value="uc">UC网盘</option>       <!-- Phase 2 后加入 -->
  <option value="baidu">百度网盘</option>  <!-- Phase 3 后加入 -->
  <option value="xunlei">迅雷网盘</option> <!-- Phase 4 后加入 -->
</select>
```

**② Cookie 输入框动态 placeholder**：
```html
<textarea x-model="form.cookie" rows="3"
  :placeholder="{
    quark:  '夸克网盘完整 Cookie',
    uc:     'UC网盘完整 Cookie',
    baidu:  '百度网盘完整 Cookie（需含 BDUSS、STOKEN 等）',
    xunlei: '迅雷网盘 refresh_token'
  }[form.pan_type] || '登录凭证'">
</textarea>
```

**③ 转存文件夹 ID 动态 placeholder**：
```html
<input x-model="form.save_folder_id"
  :placeholder="form.pan_type === 'baidu'
    ? '目录路径，如 /来自搜索站'
    : '目录 fid / id，0 表示根目录'">
```

---

## 开发顺序（支持分阶段上线）

每个 Phase 完成后即可独立部署，无需等待全部完成：

```
Phase 2（UC）    → 注册 UcProvider  + admin select 加 uc      → 部署
Phase 3（百度）  → 注册 BaiduProvider + admin select 加 baidu  → 部署
Phase 4（迅雷）  → 注册 XunleiProvider + admin select 加 xunlei → 部署
```

未注册的 pan_type 不会出现在账号管理中，系统自动降级返回 PanSou 原始链接，不影响已上线功能。

---

## 如何继续开发

告知 AI 执行某个 Phase 时，提供以下上下文：
1. 本文档：`docs/multi-netdisk-extension.md`
2. 参考 PHP 实现：`https://github.com/675061370/xinyue-search/tree/main/extend/netdisk/pan`
3. 现有夸克实现：`app/providers/quark.py`
4. 指定阶段，例如："按照 multi-netdisk-extension.md 的规划，执行 Phase 2 UC网盘开发"
