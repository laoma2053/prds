"""UC 网盘 Provider

UC 与夸克共用相同的 API 结构，仅 base URL、pan URL 和 pr 参数不同。
"""

from app.providers.quark import QuarkProvider


class UcProvider(QuarkProvider):
    """UC 网盘 Provider（继承夸克，覆盖三个配置常量）"""

    pan_type = "uc"
    BASE_URL: str = "https://pc-api.uc.cn"
    SAVE_URL: str = "https://pc-api.uc.cn"
    PAN_URL: str = "https://drive.uc.cn"
    PR: str = "UCBrowser"
