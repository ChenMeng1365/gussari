#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 客户端使用示例 (gussari Client Demo)
======================================
演示机器人如何通过 gussari 重放师获取凭据并访问网站。

用法:
    python ai_client_example.py                 # 完整流程演示
    python ai_client_example.py --quick <site> [login_url]     # 快速获取请求头
    python ai_client_example.py --browser <site> <target_url> [login_url]
                                                 # 浏览器自动化免登录演示

依赖：requests（pip install requests）
      浏览器自动化额外需要 playwright（pip install playwright）
"""

import sys

from ai_client import CredentialClient, quick_start


def demo():
    """演示完整流程"""
    client = CredentialClient()

    site = 'demo.example.com'
    target_url = 'https://demo.example.com/api/data'

    print('=== gussari 重放师 - AI 客户端示例 ===\n')

    # 1. 检查凭据状态
    print('[1] 检查 %s 的凭据状态...' % site)
    status = client.get_status(site)
    print('    状态:', status)

    if status.get('has_credentials'):
        # 2a. 已有凭据，直接获取
        print('\n[2] 凭据已存在，正在获取...')
        result = client.get_credentials(site)
    else:
        # 2b. 无凭据，发起登录请求并等待
        print('\n[2] 无凭据，发起人工登录请求...')
        login_resp = client.request_login(
            site,
            login_url='https://demo.example.com/login',
            reason='首次访问，需要人工登录认证'
        )
        print('    登录请求已创建:', login_resp)
        print('    请在 gussari 管理页中完成登录...')
        print('    等待人工输入凭据（最多120秒）...')

        result = client.get_credentials(site, wait=True, timeout=130)

    if result.get('status') == 'found':
        cred = result['data']
        print('\n[3] 凭据获取成功!')
        print('    用户名:', cred.get('username', '-'))
        print('    有Cookie:', bool(cred.get('cookies')))
        print('    有Token:', bool(cred.get('token')))

        # 3. 构建请求头
        headers = client.build_headers(cred)
        print('\n[4] 构建请求头:')
        for k, v in headers.items():
            display = v[:50] + '...' if len(str(v)) > 50 else v
            print('    %s: %s' % (k, display))

        # 4. 使用凭据访问目标网站（这里仅演示，实际使用时取消注释）
        # print('\n[5] 使用凭据访问 %s ...' % target_url)
        # if USE_REQUESTS:
        #     resp = requests.get(target_url, headers=headers)
        #     print('    响应状态码:', resp.status_code)
        #     client.report(site, resp.status_code == 200, 'Status: %d' % resp.status_code)

        print('\n[5] (示例) 使用凭据访问目标网站')
        print('    在实际使用中，这里用获取到的 headers 访问目标网站')

    elif result.get('status') == 'timeout':
        print('\n[3] 等待凭据超时，请稍后重试')
        print('    提示：确保 gussari 正在运行且有人在管理页输入凭据')
    else:
        print('\n[3] 未找到凭据:', result)

    # 5. 列出所有已保存的站点
    print('\n[6] 所有已保存凭据的站点:')
    sites = client.list_sites()
    for s in sites.get('sites', []):
        print('   -', s)
    if sites.get('count', 0) == 0:
        print('   (暂无)')

    print('\n=== 示例完成 ===')


def browser_demo(site, target_url, login_url=None):
    """浏览器自动化演示：免登录打开目标页并检测会话有效性

    用法:
        python ai_client_example.py --browser portal.company.com http://portal.company.com/home
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('[错误] 缺少 playwright，请执行: pip install playwright')
        return

    client = CredentialClient()

    with sync_playwright() as p:
        print('[1] 启动浏览器并注入 %s 的会话状态...' % site)
        browser, context = client.create_browser_context(site, p)

        if context is None:
            print('[提示] 尚未保存该站点的浏览器会话')
            print('       请先执行人工登录捕获:')
            print('       python browser_login.py %s %s' % (site, login_url or target_url))
            browser.close()
            return

        page = context.new_page()
        print('[2] 打开 %s' % target_url)
        page.goto(target_url, wait_until='domcontentloaded', timeout=60000)

        login_url = login_url or (target_url if 'login' in target_url.lower() else '')
        ok = client.is_logged_in(page, login_url)
        if ok:
            print('[3] 会话有效，已免登录进入! 页面标题:', page.title())
            client.report(site, True, '浏览器免登录访问成功')
        else:
            print('[3] 会话已失效，当前 URL:', page.url)
            client.report_expired(site, '浏览器自动化访问时发现会话失效')
            print('       已通知 gussari，请人工执行 browser_login.py 重新捕获')

        browser.close()


if __name__ == '__main__':
    if '--quick' in sys.argv:
        # 快速模式: python ai_client_example.py --quick example.com https://example.com/login
        idx = sys.argv.index('--quick')
        site = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else 'example.com'
        url = sys.argv[idx + 2] if len(sys.argv) > idx + 2 else None
        headers = quick_start(site, url)
        if headers:
            print('获取到的请求头:', headers)
    elif '--browser' in sys.argv:
        # 浏览器自动化模式: python ai_client_example.py --browser <site> <target_url> [login_url]
        idx = sys.argv.index('--browser')
        site = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else 'example.com'
        url = sys.argv[idx + 2] if len(sys.argv) > idx + 2 else 'https://example.com'
        login = sys.argv[idx + 3] if len(sys.argv) > idx + 3 else None
        browser_demo(site, url, login)
    else:
        demo()
