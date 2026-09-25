#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浏览器登录捕获器 (Browser Login Capture)
=======================================
用 Playwright 打开系统 Chrome，人工正常登录目标网站，
登录完成后自动捕获完整会话状态 (storage_state: cookies + localStorage
+ sessionStorage) 并保存到 gussari 重放师，供 AI 浏览器自动化免登录复用。

用法:
    python browser_login.py <站点标识> <登录页URL> [选项]

    站点标识: gussari 中的站点 key，如 portal.company.com 或 192.168.1.10
    登录页URL: 目标网站登录页，如 http://portal.company.com/login

选项:
    --server URL     gussari 重放师地址 (默认 http://127.0.0.1:3366)
    --token TOKEN    访问令牌 (服务端启用 token 时需要)
    --headless       无头模式 (不推荐，登录通常需要人工操作)
    --channel NAME   浏览器通道 (默认 chrome，可选 msedge)

流程:
    1. 启动 Chrome 打开登录页
    2. 你在浏览器里正常登录（输账号密码、过验证码都行）
    3. 登录成功后回到终端按回车
    4. 脚本导出 storage_state，连同 User-Agent 一起 POST 到 gussari
    5. 之后 AI 用 create_browser_context() 即可免登录操作该站

依赖: pip install playwright requests
      (浏览器用系统 Chrome，无需 playwright install)
"""

import sys
import time
import urllib.parse

try:
    import requests
except ImportError:
    print('[错误] 缺少 requests 库，请执行: pip install requests')
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('[错误] 缺少 playwright 库，请执行: pip install playwright')
    sys.exit(1)

DEFAULT_SERVER = 'http://127.0.0.1:3366'


def parse_args(argv):
    """解析命令行参数"""
    if len(argv) < 2:
        print(__doc__)
        sys.exit(1)
    opts = {
        'site': argv[0],
        'login_url': argv[1],
        'server': DEFAULT_SERVER,
        'token': '',
        'headless': False,
        'channel': 'chrome',
    }
    i = 2
    while i < len(argv):
        a = argv[i]
        if a == '--server' and i + 1 < len(argv):
            opts['server'] = argv[i + 1].rstrip('/'); i += 2
        elif a == '--token' and i + 1 < len(argv):
            opts['token'] = argv[i + 1]; i += 2
        elif a == '--channel' and i + 1 < len(argv):
            opts['channel'] = argv[i + 1]; i += 2
        elif a == '--headless':
            opts['headless'] = True; i += 1
        else:
            print('[错误] 未知参数: %s' % a)
            sys.exit(1)
    return opts


def build_api_headers(token):
    h = {'Content-Type': 'application/json'}
    if token:
        h['Authorization'] = 'Bearer ' + token
    return h


def check_server(server, token):
    """检查 gussari 重放师是否在线"""
    try:
        r = requests.get(server + '/api/sites', headers=build_api_headers(token), timeout=5)
        if r.status_code == 401:
            print('[错误] 访问被拒绝 (401)，请通过 --token 提供正确的令牌')
            sys.exit(1)
        r.raise_for_status()
        return True
    except requests.ConnectionError:
        print('[错误] 无法连接 gussari 重放师 %s' % server)
        print('       请先启动: 双击项目目录下的 start.bat')
        sys.exit(1)
    except Exception as e:
        print('[错误] gussari 响应异常: %s' % e)
        sys.exit(1)


def save_to_server(server, token, site, login_url, storage_state, user_agent, notes=''):
    """把捕获的会话状态保存到 gussari"""
    body = {
        'login_url': login_url,
        'storage_state': storage_state,
        'user_agent': user_agent,
        'notes': notes or 'Playwright 登录捕获',
        # 同时生成兼容 HTTP 请求头模式的 Cookie 串，一举两得
        'cookies': '; '.join(
            '%s=%s' % (c['name'], c['value'])
            for c in storage_state.get('cookies', [])
        ),
    }
    url = server + '/api/credentials/' + urllib.parse.quote(site)
    r = requests.post(url, headers=build_api_headers(token), json=body, timeout=10)
    r.raise_for_status()
    return r.json()


def main():
    opts = parse_args(sys.argv[1:])
    site = opts['site']
    login_url = opts['login_url']

    print('=' * 56)
    print('  浏览器登录捕获器 (gussari)')
    print('=' * 56)
    print('  站点: %s' % site)
    print('  登录页: %s' % login_url)
    print('  凭据服务: %s' % opts['server'])
    print('=' * 56)

    check_server(opts['server'], opts['token'])

    with sync_playwright() as p:
        print('\n[1] 正在启动浏览器...')
        browser = p.chromium.launch(
            channel=opts['channel'],
            headless=opts['headless'],
        )
        context = browser.new_context()
        page = context.new_page()
        ua = page.evaluate('() => navigator.userAgent')

        print('[2] 打开登录页: %s' % login_url)
        try:
            page.goto(login_url, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            print('[警告] 页面加载较慢 (%s)，继续...' % e)

        print()
        print('-' * 56)
        print('  请在弹出的浏览器窗口中完成登录')
        print('  登录成功、看到目标页面后，回到本窗口按回车')
        print('-' * 56)

        try:
            input('  登录完成后按回车继续 > ')
        except (EOFError, KeyboardInterrupt):
            print('\n[取消] 用户中断，未保存任何数据')
            browser.close()
            sys.exit(0)

        print('\n[3] 正在捕获会话状态...')
        time.sleep(1)  # 给最后的异步 Cookie 写入留一点时间
        storage_state = context.storage_state()

        cookie_count = len(storage_state.get('cookies', []))
        ls_count = sum(len(v) for v in storage_state.get('origins', []))
        print('    捕获到 %d 个 Cookie，%d 条 localStorage 记录' % (cookie_count, ls_count))

        if cookie_count == 0 and ls_count == 0:
            print('[警告] 未捕获到任何会话数据，可能尚未登录成功')
            try:
                confirm = input('    仍要保存吗？(y/N) > ').strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = 'n'
            if confirm != 'y':
                print('[取消] 未保存')
                browser.close()
                sys.exit(0)

        print('\n[4] 保存到 gussari...')
        try:
            result = save_to_server(
                opts['server'], opts['token'], site, login_url,
                storage_state, ua,
            )
        except Exception as e:
            print('[错误] 保存失败: %s' % e)
            browser.close()
            sys.exit(1)

        if result.get('status') == 'saved':
            print('    已保存! 站点标识: %s' % site)
            print()
            print('[完成] AI 端现在可以通过以下方式免登录访问:')
            print("    from ai_client import CredentialClient")
            print("    client = CredentialClient()")
            print("    ctx = client.create_browser_context(site, playwright)")
        else:
            print('[错误] 服务端返回异常: %s' % result)

        print('\n[提示] 浏览器即将关闭...')
        time.sleep(1)
        browser.close()


if __name__ == '__main__':
    main()
