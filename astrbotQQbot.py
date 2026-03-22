"""
影视资源QQ搜索插件 for AstrBot
版本：2.0.0
作者：www.zhuiju.us
说明：通过 PRDS 资源中台 API 实现全网影视资源搜索

支持命令：
- 搜 <关键词>：搜索影视资源（默认夸克网盘）
- 搜索 <关键词>：同上
- 搜 <关键词> <网盘类型>：指定网盘类型搜索
- 搜索 <关键词> <网盘类型>：同上

使用示例：
- 搜权力的游戏
- 搜索 庆余年 百度
- 搜 流浪地球 阿里
- 搜 三体 夸克网盘
"""

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

import re
import aiohttp
import asyncio


# PanSou 网盘类型别名映射表
# 键: 用户可能输入的各种别名（小写）  值: PanSou API 标准 pan_type
_PAN_TYPE_ALIASES: dict[str, str] = {
    # 夸克网盘
    "quark": "quark", "夸克": "quark", "夸克网盘": "quark", "夸克盘": "quark",
    "夸克云盘": "quark", "夸克云": "quark",
    # 百度网盘
    "baidu": "baidu", "百度": "baidu", "百度网盘": "baidu", "百度盘": "baidu",
    "百度云盘": "baidu", "百度云": "baidu",
    # 阿里云盘
    "aliyun": "aliyun", "ali": "aliyun", "阿里": "aliyun", "阿里云盘": "aliyun",
    "阿里盘": "aliyun", "阿里网盘": "aliyun", "阿里云": "aliyun",
    "aliyundrive": "aliyun",
    # 天翼云盘
    "tianyi": "tianyi", "天翼": "tianyi", "天翼云盘": "tianyi", "天翼盘": "tianyi",
    "天翼网盘": "tianyi", "天翼云": "tianyi",
    # 移动云盘
    "mobile": "mobile", "移动": "mobile", "移动云盘": "mobile", "移动盘": "mobile",
    "移动云": "mobile", "中国移动": "mobile",
    # 115网盘
    "115": "115", "115网盘": "115", "115盘": "115",
    # PikPak
    "pikpak": "pikpak", "pk": "pikpak",
    # 迅雷网盘
    "xunlei": "xunlei", "迅雷": "xunlei", "迅雷网盘": "xunlei", "迅雷盘": "xunlei",
    "迅雷云盘": "xunlei",
    # 123盘
    "123": "123", "123盘": "123", "123网盘": "123", "123pan": "123",
    "123云盘": "123",
    # UC网盘
    "uc": "uc", "uc网盘": "uc", "uc盘": "uc", "uc云盘": "uc",
    # 磁力链接
    "magnet": "magnet", "磁力": "magnet", "磁力链接": "magnet",
    # ED2K
    "ed2k": "ed2k", "电驴": "ed2k",
}

# 网盘类型的中文显示名
_PAN_TYPE_DISPLAY: dict[str, str] = {
    "quark": "夸克网盘", "baidu": "百度网盘", "aliyun": "阿里云盘",
    "tianyi": "天翼云盘", "mobile": "移动云盘", "115": "115网盘",
    "pikpak": "PikPak", "xunlei": "迅雷网盘", "123": "123盘",
    "uc": "UC网盘", "magnet": "磁力链接", "ed2k": "ED2K",
}


def _resolve_pan_type(text: str) -> str | None:
    """从用户输入文本中解析网盘类型

    仅精确匹配别名表，避免模糊匹配导致关键词被误截断。
    返回 PanSou 标准 pan_type，无法识别返回 None。
    """
    normalized = text.strip().lower()
    if not normalized:
        return None

    # 仅精确匹配
    return _PAN_TYPE_ALIASES.get(normalized)


def _parse_keyword_and_pan_type(raw_input: str) -> tuple[str, str | None]:
    """从用户输入中分离关键词和网盘类型

    策略：从末尾向前扫描，尝试匹配网盘类型别名。
    支持有空格和无空格的情况。

    示例：
      "流浪地球 百度网盘"  → ("流浪地球", "baidu")
      "流浪地球百度盘"      → ("流浪地球", "baidu")
      "流浪地球 baidu"      → ("流浪地球", "baidu")
      "流浪地球"            → ("流浪地球", None)
    """
    text = raw_input.strip()
    if not text:
        return ("", None)

    # 先尝试按空格分割，检查最后一段是否为网盘类型
    parts = text.rsplit(maxsplit=1)
    if len(parts) == 2:
        pan_type = _resolve_pan_type(parts[1])
        if pan_type:
            return (parts[0].strip(), pan_type)

    # 无空格或尾部不是网盘类型：从末尾逐步截取尝试匹配
    # 网盘别名最长约6个字符（如"阿里云盘"、"百度网盘"、"aliyundrive"）
    max_suffix_len = min(len(text), 12)
    for suffix_len in range(max_suffix_len, 0, -1):
        suffix = text[-suffix_len:]
        pan_type = _resolve_pan_type(suffix)
        if pan_type:
            keyword = text[:-suffix_len].strip()
            if keyword:
                return (keyword, pan_type)

    # 无法识别网盘类型，整段作为关键词
    return (text, None)


@register("QQ_search", "zhuiju.us", "影视资源QQ搜索插件（PRDS）", "2.0.0", "https://github.com/yourname/repo")
class VideoSearchPlugin(Star):
    """影视资源QQ搜索插件 - 对接 PRDS 资源中台"""

    def __init__(self, context: Context):
        super().__init__(context)

        # ==================== 配置区域（请修改） ====================
        # PRDS API 地址（必改）
        self.api_url = "http://prds:8000/api/v1/search"

        # 默认网盘类型（用户未指定时使用）
        self.default_pan_type = "quark"

        # 默认返回资源数量（固定2条，返回最新的2个资源）
        self.default_limit = 2

        # 调用方标识（用于 PRDS 后台统计区分来源）
        self.client_id = "qq-bot"

        # 请求超时时间（秒）- PRDS 转存模式耗时较长，建议 60 秒
        self.timeout = 60

        # 是否启用调试日志
        self.debug = False
        # ============================================================

        logger.info("影视资源QQ搜索插件已加载（PRDS v2.0）")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def message_handler(self, event: AstrMessageEvent):
        """消息处理器"""
        message = event.message_str.strip()

        # 检查是否是搜索命令
        if not message.startswith("搜"):
            return

        # 提取原始输入（去掉"搜"或"搜索"前缀）
        if len(message) > 1 and message[1] == "索":
            raw_input = message[2:].strip()
        else:
            raw_input = message[1:].strip()

        # 参数验证
        if not raw_input:
            yield event.plain_result(
                "请输入搜索关键词\n\n"
                "使用方法：\n"
                "- 搜 权力的游戏\n"
                "- 搜索 庆余年\n"
                "- 搜 流浪地球 百度\n"
                "- 搜 三体 阿里云盘"
            )
            return

        # 分离关键词和网盘类型
        keyword, pan_type = _parse_keyword_and_pan_type(raw_input)
        if not keyword:
            yield event.plain_result("请输入搜索关键词，网盘类型放在关键词后面")
            return

        pan_type = pan_type or self.default_pan_type
        pan_display = _PAN_TYPE_DISPLAY.get(pan_type, pan_type)

        if self.debug:
            logger.debug(f"搜索请求: keyword={keyword}, pan_type={pan_type} (来自 {event.get_sender_id()})")

        # 发送等待提示
        yield event.plain_result(f"正在搜索 [{pan_display}] 资源，请稍等...")

        # 执行搜索
        try:
            response = await self._fetch_prds(keyword, pan_type)
            result_message = self._format_response(keyword, pan_type, response)
            if result_message:
                yield event.plain_result(result_message)

        except asyncio.TimeoutError:
            yield event.plain_result(
                "搜索超时\n\n"
                "可能原因：\n"
                "- 网络波动或服务器繁忙\n"
                "- 资源转存中，耗时较长\n\n"
                "建议稍后再试，或简化关键词"
            )
            logger.error(f"搜索超时: {keyword}")

        except aiohttp.ClientError as e:
            yield event.plain_result(f"网络连接失败: {str(e)}")
            logger.error(f"网络错误: {keyword}, {str(e)}")

        except Exception as e:
            yield event.plain_result(f"搜索出错: {str(e)}")
            logger.error(f"未知错误: {keyword}, {str(e)}", exc_info=True)

    async def _fetch_prds(self, keyword: str, pan_type: str) -> dict:
        """调用 PRDS 搜索 API

        Args:
            keyword: 搜索关键词
            pan_type: 网盘类型（PanSou 标准值）

        Returns:
            PRDS API 返回的 JSON 数据
        """
        payload = {
            "keyword": keyword,
            "pan_type": pan_type,
            "limit": self.default_limit,
            "client_id": self.client_id,
        }

        if self.debug:
            logger.debug(f"PRDS 请求: {self.api_url} payload={payload}")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "AstrBot-VideoSearch/2.0",
                },
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"API 返回错误 {resp.status}: {error_text}")

                data = await resp.json()

                if self.debug:
                    logger.debug(f"PRDS 返回: success={data.get('success')}, code={data.get('code')}")

                return data

    def _format_response(self, keyword: str, pan_type: str, response: dict) -> str:
        """格式化 PRDS 返回结果为 QQ 消息文本

        Args:
            keyword: 搜索关键词
            pan_type: 网盘类型
            response: PRDS API 返回数据

        Returns:
            格式化后的消息文本
        """
        success = response.get("success", False)
        message = response.get("message", "未知错误")

        if not success:
            code = response.get("code", "UNKNOWN")
            return f"搜索失败 ({code}): {message}"

        data = response.get("data", {})
        results = data.get("results", [])

        if not results:
            return (
                f"未找到「{keyword}」相关资源\n\n"
                f"建议：\n"
                f"- 检查是否有错别字\n"
                f"- 尝试简化关键词\n"
                f"- 换个表述方式\n\n"
                f"示例：搜庆余年"
            )

        pan_display = _PAN_TYPE_DISPLAY.get(pan_type, pan_type)
        lines = []

        # 标题
        lines.append("")
        lines.append(f"🔍 {keyword} 丨[{pan_display}] 资源")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("")

        # 遍历结果
        for idx, item in enumerate(results, 1):
            title = item.get("title", "未知标题")
            url = item.get("url", "")
            password = item.get("password")

            lines.append(f"{idx}. {title}")
            if password:
                lines.append(f"🔗 {url}")
                lines.append(f"🔑 提取码: {password}")
            else:
                lines.append(f"🔗 {url}")
            lines.append("")

        # 底部提示（保持原样不动）
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("‼️ 5分钟后自动删除，及时保存")
        lines.append("🈲 不要相信网盘内的任何广告")

        logger.info(f"搜索成功: {keyword} [{pan_type}] 返回 {len(results)} 条结果")

        return "\n".join(lines)

    async def terminate(self):
        """插件卸载时调用"""
        logger.info("影视资源QQ搜索插件已卸载")
