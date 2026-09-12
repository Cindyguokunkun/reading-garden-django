import http.cookiejar
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'

class HttpError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

class HttpTimeout(HttpError):
    pass

def build_opener(proxy='', cookies=True):
    # An empty proxy must be passed explicitly: urllib otherwise honours the system HTTP(S)_PROXY.
    handlers = [urllib.request.ProxyHandler({'http': proxy, 'https': proxy} if proxy else {})]
    if cookies: handlers.append(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [('User-Agent', USER_AGENT)]
    return opener

def fetch(opener, url, *, data=None, headers=None, timeout=15, retries=0):
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
