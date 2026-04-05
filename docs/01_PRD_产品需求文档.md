
# PRD 产品需求

目标：
构建一个资源中台，让多个系统可以调用资源搜索并获得网盘分享链接。

核心流程：

用户请求
→ 前端系统
→ PRDS API
→ PanSou 搜索
→ 资源候选池
→ 账号池调度
→ 网盘转存
→ 生成分享
→ 返回资源
→ 生命周期删除

关键策略：

1. 资源10分钟删除
2. 提示用户尽快转存
3. 降低网盘容量成本
4. Redis缓存减少重复转存

补充需求：
1. 当用户输入的需求里包括指定的网盘类型，则调用pansou的指定网盘类型资源；如果没有输入指定的网盘类型，则默认夸克网盘调用pansou
2. 如果后台配置了转存、分享、删除的网盘类型账号，则执行转存分享删除流程
3. 如果用户输入的网盘类型，在后台没有配置对应网盘类型账号，则不执行转存分享删除流程，直接返回pansou的资源链接
4. 需要一个后台界面，配置网盘账号，查看api调用相关数据（搜索次数、资源数、各网盘类型的资源数、转存成功数、分享成功数、删除数等等）
5. 容器日志需要enmoj表情和必要的中文提示

待办需求（后期迭代）：

1. PanSou 返回的资源字段未完整保存，当前缺少以下字段需补齐：
   - source: 资源来源（如 tg:dianying4k, plugin:labi 等），用于前端展示来源渠道
   - images: 资源封面图片列表，用于前端展示缩略图/海报
   - datetime: 资源原始发布时间，用于前端按时间排序展示
   - note: 当前仅作为 title 兜底，未独立保存，前端可能需要完整描述
   涉及修改的文件：
   - pansou_client.py -- PanSouLink 添加 source/images 字段解析
   - models/resource.py -- ResourceAsset 表添加 source/images/original_datetime 字段
   - services/resource_service.py -- 转存写入新字段 + API 返回携带新字段
   - migrations/ -- 新增数据库迁移（给已有表加字段）
   - static/admin.html -- 管理后台展示新字段（可选）

2. 分享链接有效期自定义（方案待定）：
   - 当前状态：夸克 QuarkProvider 创建分享时 expired_type=1（1天过期），写死在代码中
   - 需求：支持自定义分享链接的有效期
   - 可选方案（未决定，需进一步评估）：
     a. 前端请求时传入：搜索接口新增 share_expire 参数，由前端用户决定有效期
     b. PRDS 全局配置：在 .env 中设置 SHARE_EXPIRE_TYPE，所有分享统一有效期
     c. 按账号配置：pan_accounts 表新增 share_expire_type 字段，每个账号独立设置
     d. 混合方案：PRDS 设默认值，前端可覆盖，但不超过账号级别的上限
   - 涉及修改的文件：
     - providers/quark.py -- create_share() 中 expired_type 参数化
     - providers/base.py -- create_share() 签名可能需要新增 expire 参数
     - 其他文件取决于最终选择的方案

3. 删除任务确认机制缺失 + 批量删除风控：
   - 当前问题：
     a. delete_resource() 调用夸克删除 API 后，无论实际是否删除成功，都标记为 success=True（bug）
     b. 管理后台统计"已删除"数量与网盘实际不一致（已删除35 vs 网盘仍存在10个）
     c. 短时间大量删除可能触发夸克网盘限流或静默失败
   - 推荐方案（A + B + D 组合）：
     a. 批间延迟：每删一个文件后 sleep 3-5 秒，避免短时间密集请求
     b. 分批限量：每轮扫描最多删 5 个（当前 limit=20 改小），剩余下轮再删
     c. 失败重试：标记失败的任务加入重试队列，最多重试 3 次后放弃并告警
   - 涉及修改的文件：
     - providers/quark.py -- delete_resource() 修复：检查 _query_task 返回状态，失败则返回 success=False
     - workers/delete_worker.py -- 添加批间延迟 + 分批限量 + 重试计数逻辑
     - models/task.py -- DeleteTask 表可能需要新增 retry_count 字段
     - migrations/ -- 新增数据库迁移

4. 容器日志缺少时间戳：
   - 当前问题：entrypoint.sh 中 echo 输出的启动日志、以及 FastAPI print 输出没有日期前缀，不方便生产环境排查问题
   - 需求：所有容器日志统一带上时间戳，格式如 `2026-03-21 18:30:00 🚀 PRDS 启动`
   - 涉及修改：
     - entrypoint.sh -- echo 前加日期，或用 logger 替代
     - app/main.py -- lifespan 中 print 改为 logging
     - 统一日志格式配置，确保 uvicorn / worker / 启动脚本三者格式一致

5. 搜索结果返回时缺少汇总日志：
   - 当前问题：转存分享成功后、或无账号返回原始链接时，容器日志没有最终结果提示
   - 需求：在每次搜索请求结束时，输出一条汇总日志，说明返回了几条结果、哪些转存成功、哪些降级返回原始链接
   - 示例日志：
     - `✅ [搜索完成] keyword=流浪地球, 模式=proxy, 转存成功=3条, 耗时=12.5秒`
     - `📎 [搜索完成] keyword=流浪地球, 模式=direct, 返回原始链接=5条（无quark账号配置）, 耗时=2.1秒`
   - 涉及修改：
     - services/resource_service.py -- search_and_deliver 方法末尾添加汇总日志

参考开源项目

1. **PanCheck**: https://github.com/Lampon/PanCheck
2. **quark-auto-save**: https://github.com/Cp0204/quark-auto-save
3. **quark-save**: https://github.com/henggedaren/quark-save
4. **pansou**: https://github.com/fish2018/pansou
5. **xinyue-search**: https://github.com/675061370/xinyue-search
