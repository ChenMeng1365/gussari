#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 凭据客户端 (gussari Client SDK)
=================================
AI Agent / 爬虫 / 自动化工具集成 gussari 重放师的客户端库，
实现"首次人工登录、后续自动加载"的凭据管理。

两种使用模式:
    1. HTTP 请求模式: build_headers() 拿请求头直接调 API
    2. 浏览器自动化模式: create_browser_context() 注入会话状态免登录操作网页
       (需 playwright，配合 browser_login.py 人工登录捕获)

依赖：requests（pip install requests）
      浏览器自动化额外需要 playwright（pip install playwright）

用法:
    from ai_client import CredentialClient
    client = CredentialClient()
    result = client.get_credentials('example.com')
"""

import json
import urllib.parse

try:
    import requests
    USE_REQUESTS = True
except ImportError:
    USE_REQUESTS = False
    import urllib.request
    import urllib.error

# ==================== 配置 ====================
CREDENTIAL_SERVER = 'http://127.0.0.1:3366'  # gussari 重放师地址


class CredentialClient:
    """gussari 重放师 AI 客户端"""

    def __init__(self, base_url=CREDENTIAL_SERVER):
        self.base = base_url.rstrip('/')

    def _request(self, method, path, data=None, timeout=None):
        """发送 HTTP 请求"""
        url = self.base + path
        if USE_REQUESTS:
            kwargs = {'timeout': timeout or 10}
            if data is not None:
                kwargs['json'] = data
            resp = requests.request(method, url, **kwargs)
            return resp.json()
        else:
            # 使用标准库 urllib
            headers = {}
            body = None
            if data is not None:
                body = json.dumps(data).encode('utf-8')
                headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout or 10) as resp:
                return json.loads(resp.read().decode('utf-8'))

    def list_sites(self):
        """列出所有已保存凭据的站点"""
        return self._request('GET', '/api/sites')

    def get_credentials(self, site, wait=False, timeout=130):
        """获取指定站点的凭据

        Args:
            site: 站点标识，如 'example.com'
            wait: 是否长轮询等待凭据（首次登录时使用）
            timeout: 长轮询超时秒数

        Returns:
            dict: {'status': 'found', 'data': {...}} 或 {'status': 'not_found'/'timeout'}
        """
        path = '/api/credentials/' + urllib.parse.quote(site)
        if wait:
            path += '?wait=true'
        return self._request('GET', path, timeout=timeout)

    def save_credentials(self, site, username='', password='', cookies='', token='', headers=None, login_url='', notes=''):
        """保存/更新凭据"""
        data = {
            'username': username,
            'password': password,
            'cookies': cookies,
            'token': token,
            'headers': headers or {},
            'login_url': login_url,
            'notes': notes
        }
        return self._request('POST', '/api/credentials/' + urllib.parse.quote(site), data=data)

    def delete_credentials(self, site):
        """删除凭据"""
        return self._request('DELETE', '/api/credentials/' + urllib.parse.quote(site))

    def request_login(self, site, login_url='', reason='机器人需要人工登录'):
        """发起人工登录请求（gussari 会自动弹出管理页提醒人工）"""
        data = {'login_url': login_url, 'reason': reason}
        return self._request('POST', '/api/request-login/' + urllib.parse.quote(site), data=data)

    def list_pending(self):
        """列出待处理的登录请求"""
        return self._request('GET', '/api/pending')

    def complete_login(self, request_id, username='', password='', cookies='', token=''):
        """完成登录请求"""
        data = {
            'username': username,
            'password': password,
            'cookies': cookies,
            'token': token
        }
        return self._request('POST', '/api/complete-login/' + request_id, data=data)

    def report(self, site, success, message=''):
        """报告凭据使用结果"""
        data = {'success': success, 'message': message}
        return self._request('POST', '/api/report/' + urllib.parse.quote(site), data=data)

    def get_status(self, site):
        """查询凭据状态"""
        return self._request('GET', '/api/status/' + urllib.parse.quote(site))

    def build_headers(self, credentials):
        """将凭据转换为 HTTP 请求头

        Args:
            credentials: get_credentials() 返回的 data 字段

        Returns:
            dict: 可直接传给 requests 的 headers
        """
        headers = {}
        if credentials.get('cookies'):
            headers['Cookie'] = credentials['cookies']
        if credentials.get('token'):
            headers['Authorization'] = 'Bearer ' + credentials['token']
        if credentials.get('headers'):
            headers.update(credentials['headers'])
        # 关键：UA 与人工登录时保持一致，避免站点指纹校验踢会话
        if credentials.get('user_agent'):
            headers.setdefault('User-Agent', credentials['user_agent'])
        return headers

    # ==================== 浏览器自动化模式 ====================

    def get_storage_state(self, site):
        """获取站点的 Playwright storage_state（需先用 browser_login.py 捕获）"""
        result = self.get_credentials(site)
        if result.get('status') == 'found':
            state = result['data'].get('storage_state')
            if state:
                return state
            return None
        return None

    def create_browser_context(self, site, playwright, headless=True,
                               browser_channel='chrome'):
        """创建注入了已保存会话状态的浏览器上下文（浏览器自动化入口）

        用 browser_login.py 人工登录捕获一次后，之后 AI 调本方法即可
        免登录操作目标网站（Cookie + localStorage + sessionStorage 全量注入）。

        Args:
            site: 站点标识（须与 browser_login.py 保存时一致）
            playwright: sync_playwright() 返回的 playwright 实例
            headless: 是否无头模式（AI 自动化一般用 True）
            browser_channel: 浏览器通道，默认系统 chrome，可选 msedge

        Returns:
            (browser, context): 已注入状态的 Browser 和 BrowserContext
            若无已保存会话则返回 (browser, None)，调用方需走人工登录流程
        """
        state = self.get_storage_state(site)

        # UA 必须与登录捕获时一致，否则部分站点会因指纹突变踢掉会话
        ua = None
        if state is None:
            # 先取已存凭据里的 UA（可能有但无 storage_state）
            result = self.get_credentials(site)
            if result.get('status') == 'found':
                ua = result['data'].get('user_agent') or None

        browser = playwright.chromium.launch(
            channel=browser_channel,
            headless=headless,
        )

        if state is None:
            return browser, None

        kwargs = {'storage_state': state}
        if ua:
            kwargs['user_agent'] = ua
        context = browser.new_context(**kwargs)
        return browser, context

    def is_logged_in(self, page, login_url, check_text=None, timeout=8000):
        """检测页面是否处于已登录状态（未跳回登录页且可选关键词存在）

        Args:
            page: Playwright Page
            login_url: 登录页 URL，页面跳回它则视为会话失效
            check_text: 可选，页面需包含的文本（如用户名/退出按钮）
            timeout: 等待页面稳定的毫秒数
        """
        try:
            page.wait_for_load_state('domcontentloaded', timeout=timeout)
        except Exception:
            pass
        url = page.url
        # 未跳回登录页
        if login_url and login_url.split('?')[0] in url.split('?')[0]:
            return False
        if check_text:
            try:
                content = page.content()
                if check_text not in content:
                    return False
            except Exception:
                return False
        return True

    def report_expired(self, site, message='会话已失效'):
        """会话失效时报告并自动发起人工重新登录请求

        调用后 gussari 会弹窗提醒人工，人工执行 browser_login.py 重新捕获
        （或直接在管理页填新 Cookie），AI 可用 get_credentials(wait=True) 等待。
        """
        self.report(site, False, message)
        self.request_login(
            site,
            reason='AI 浏览器自动化检测到会话失效，需要人工重新登录',
        )


def quick_start(site, target_url=None):
    """快速获取凭据的便捷函数

    用法:
        headers = quick_start('example.com')
        # 然后用 headers 访问网站
        resp = requests.get('https://example.com/api', headers=headers)
    """
    client = CredentialClient()

    # 尝试直接获取
    result = client.get_credentials(site)
    if result.get('status') == 'found':
        headers = client.build_headers(result['data'])
        return headers

    # 无凭据，发起请求并等待
    print('[gussari] %s 无凭据，发起人工登录请求...' % site)
    client.request_login(site, login_url=target_url or '', reason='机器人自动触发登录')
    print('[gussari] 请在管理页完成登录...')

    result = client.get_credentials(site, wait=True, timeout=130)
    if result.get('status') == 'found':
        print('[gussari] 凭据获取成功!')
        return client.build_headers(result['data'])
    else:
        print('[gussari] 获取凭据失败:', result.get('status'))
        return None
