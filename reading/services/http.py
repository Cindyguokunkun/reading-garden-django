"""基于标准库 urllib 的轻量 HTTP 客户端封装。

本模块为各外部服务（AR BookFinder、封面检索、AI 出题）提供统一的
请求发送、代理与 Cookie 处理、超时与重试、以及一致的错误类型，
避免在各服务中重复处理 urllib 的底层细节。
"""

import http.cookiejar
import time
import urllib.error
import urllib.parse
import urllib.request

# 统一的浏览器 User-Agent，降低被目标站点按爬虫拦截的概率。
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'


class HttpError(Exception):
    """HTTP 请求失败的通用异常。

    Attributes:
        status (int | None): 若失败源自服务器响应，则为对应的 HTTP 状态码；
            网络层错误时为 ``None``。
    """

    def __init__(self, message, status=None):
        """初始化异常。

        Args:
            message (str): 错误描述信息。
            status (int | None): 可选的 HTTP 状态码，默认 ``None``。
        """
        super().__init__(message)
        self.status = status


class HttpTimeout(HttpError):
    """请求超时异常，是 :class:`HttpError` 的特化类型。"""

    pass


def build_opener(proxy='', cookies=True):
    """构建带指定代理与 Cookie 策略的 urllib opener。

    Args:
        proxy (str): 代理地址。传入空字符串表示显式不使用代理——
            这一点很重要，否则 urllib 会自动采用系统的 HTTP(S)_PROXY 环境变量。
        cookies (bool): 是否启用 Cookie 处理。需要维持会话（如 AR BookFinder
            的多步表单）时应为 ``True``；无状态 API 调用可设为 ``False``。

    Returns:
        urllib.request.OpenerDirector: 已配置代理处理器、可选 Cookie 处理器
        并设置了统一 User-Agent 头的 opener。
    """
    # An empty proxy must be passed explicitly: urllib otherwise honours the system HTTP(S)_PROXY.
    handlers = [urllib.request.ProxyHandler({'http': proxy, 'https': proxy} if proxy else {})]
    if cookies: handlers.append(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [('User-Agent', USER_AGENT)]
    return opener


def fetch(opener, url, *, data=None, headers=None, timeout=15, retries=0):
    """通过给定 opener 发起一次请求，返回状态、文本与最终 URL。

    支持 GET（``data`` 为空）与 POST（``data`` 为字典或已编码字节）。
    针对网络层错误按 ``retries`` 进行有限次重试，重试间隔线性递增；
    HTTP 错误状态不重试，直接抛出。

    Args:
        opener (urllib.request.OpenerDirector): 由 :func:`build_opener` 构建的 opener。
        url (str): 目标请求地址。
        data (dict | bytes | None): 请求体。字典会被 urlencode 后编码为字节；
            为 ``None`` 时发送 GET 请求。
        headers (dict | None): 附加的请求头，默认无。
        timeout (int): 单次请求超时时间（秒），默认 15。
        retries (int): 网络层错误的额外重试次数，默认 0（即只尝试一次）。

    Returns:
        tuple[int, str, str]: ``(status, text, final_url)``——HTTP 状态码、
        按响应字符集解码（无法识别时以 utf-8、错误替换）后的正文文本，
        以及跟随重定向后的最终 URL。

    Raises:
        HttpTimeout: 超过重试次数后仍因超时失败。
        HttpError: 收到 HTTP 错误状态，或超过重试次数后仍因其他网络原因失败。
    """
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    for attempt in range(retries + 1):
        try:
            with opener.open(urllib.request.Request(url, data=body, headers=headers or {}), timeout=timeout) as response:
                charset = response.headers.get_content_charset() or 'utf-8'
                return response.status, response.read().decode(charset, 'replace'), response.geturl()
        except urllib.error.HTTPError as error:
            raise HttpError(f'HTTP {error.code}', status=error.code) from None
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, 'reason', error)
            if attempt >= retries:
                if isinstance(reason, TimeoutError): raise HttpTimeout('timeout') from None
                raise HttpError(str(reason)) from None
            time.sleep(0.5 * (attempt + 1))
