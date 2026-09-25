# -*- coding: utf-8 -*-
# ============================================================
# capture.py — gussari 浏览器自动化层
# ============================================================
# 职责:
#   - 网页登录捕获（Playwright 打开 Chrome/Edge，人工登录后一键保存会话）
#   - 用已保存凭据直接打开网站（注入 Cookie / storage_state 免登录）
#   - playwright 依赖探测与一键 pip 安装（进度可视化）
#
# 线程模型:
#   Playwright sync API 不允许跨线程调用，每个捕获/打开会话由专用
#   daemon 线程运行，主线程经 session dict + store.LOCK 轮询通信。
# ============================================================

import importlib.util
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime

import config
import store

LOCK = store.LOCK  # 共用同一把全局锁

# 会话状态（内存态，不持久化）
_captures = {}   # {site: {status, login_url, channel, current_url, error, cmd, ...}} 网页登录捕获会话
_opens = {}      # {site: {status, error, started_at, ...}} 用已保存凭据打开网站的会话


# ==================== playwright 依赖 ====================

def has_playwright():
    """检测 playwright 是否可用（仅探测模块存在性，不实际加载）"""
    try:
        if importlib.util.find_spec('playwright') is None:
            return False
        if importlib.util.find_spec('playwright.sync_api') is None:
            return False
        if importlib.util.find_spec('greenlet') is None:
            return False
        return True
    except Exception:
        return False


# 一键安装 playwright（网页可视化进度，彻底避免 pip 装错 Python 环境的问题）
_install_state = {
    'running': False,
    'result': None,   # 'success' / 'failed'
    'log': [],
    'finished_at': None,
}


def _pip_install_worker():
    """用运行服务的同一个 Python 后台安装 playwright，进度实时写入 _install_state"""
    cmd = [sys.executable, '-m', 'pip', 'install', 'playwright',
           '-i', config.PIP_MIRROR, '--trusted-host', 'pypi.tuna.tsinghua.edu.cn']
    with LOCK:
        _install_state['log'].append('正在执行: %s' % ' '.join(cmd))
    proc = None
    ok = False
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, errors='replace')
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            with LOCK:
                _install_state['log'].append(line)
                if len(_install_state['log']) > 300:
                    _install_state['log'] = _install_state['log'][-200:]
        proc.wait()
        ok = proc.returncode == 0
    except Exception as e:
        with LOCK:
            _install_state['log'].append('安装进程异常: %s' % e)
    installed = has_playwright()
    with LOCK:
        _install_state['running'] = False
        _install_state['result'] = 'success' if (ok and installed) else 'failed'
        if ok and not installed:
            _install_state['log'].append('[警告] pip 显示成功但 playwright 仍不可用，请把以上日志反馈')
        _install_state['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def start_pip_install():
    """启动安装线程；已在安装中则返回 False"""
    with LOCK:
        if _install_state['running']:
            return False
        _install_state['running'] = True
        _install_state['result'] = None
        _install_state['log'] = []
        _install_state['finished_at'] = None
    threading.Thread(target=_pip_install_worker, daemon=True).start()
    return True


def get_install_state():
    """安装状态快照（含 playwright 可用性，供管理页渲染）"""
    with LOCK:
        state = {
            'running': _install_state['running'],
            'result': _install_state['result'],
            'finished_at': _install_state['finished_at'],
            'log': list(_install_state['log'][-40:]),
        }
    state['python'] = sys.executable
    state['playwright'] = has_playwright()
    return state


# ==================== 网页登录捕获 ====================

def capture_worker(site, login_url, channel, session):
    """网页登录捕获线程：Playwright 的全部操作都在本线程内完成

    （Playwright sync API 不允许跨线程调用，因此用专用线程 + 状态轮询通信。）
    """
    pw = None
    browser = None
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        launch_kwargs = {'headless': False}
        if channel:
            launch_kwargs['channel'] = channel
        browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context()
        page = context.new_page()
        ua = page.evaluate('() => navigator.userAgent')

        try:
            page.goto(login_url, wait_until='domcontentloaded', timeout=60000)
        except Exception:
            pass  # 页面加载慢不致命，用户仍可在浏览器中手动输入地址

        # 等待用户在网页上点「保存」或「取消」
        deadline = time.time() + config.CAPTURE_TIMEOUT
        cmd = None
        while True:
            with LOCK:
                cmd = session.get('cmd')
            if cmd:
                break
            try:
                url = page.url
                with LOCK:
                    session['current_url'] = url
            except Exception:
                pass
            if time.time() > deadline:
                cmd = 'timeout'
                break
            time.sleep(0.8)

        if cmd == 'complete':
            time.sleep(1)  # 给最后的异步 Cookie 写入留一点时间
            state = context.storage_state()
            cookie_count = len(state.get('cookies', []))
            body = {
                'login_url': login_url,
                'storage_state': state,
                'user_agent': ua,
                'notes': '网页一键登录捕获',
                # 同时生成兼容 HTTP 请求头模式的 Cookie 串，一举两得
                'cookies': '; '.join(
                    '%s=%s' % (c['name'], c['value'])
                    for c in state.get('cookies', [])
                ),
            }
            store.store_credential(site, body)
            with LOCK:
                session['status'] = 'completed'
                session['cookies_count'] = cookie_count
        elif cmd == 'cancel':
            with LOCK:
                session['status'] = 'cancelled'
        else:
            with LOCK:
                session['status'] = 'timeout'
                session['error'] = '捕获超时（超过 %d 分钟未完成），请重新发起' % (config.CAPTURE_TIMEOUT // 60)
    except ImportError as e:
        with LOCK:
            session['status'] = 'failed'
            session['error'] = ('playwright 不可用(%s)。请在上方点击「一键安装 playwright」；或手动执行: "%s" -m pip install playwright' % (str(e), sys.executable))[:300]
    except Exception as e:
        msg = str(e)
        hint = ''
        if "Executable doesn't exist" in msg:
            hint = '（未找到对应浏览器，请安装 Chrome/Edge 或切换浏览器通道）'
        with LOCK:
            session['status'] = 'failed'
            session['error'] = ('浏览器启动/操作失败%s: %s' % (hint, msg))[:300]
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        with LOCK:
            session['cmd'] = None
            session['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def start_capture(site, login_url, channel):
    """启动一次登录捕获会话。成功返回 (session, None)，失败返回 (None, error)"""
    with LOCK:
        session = _captures.get(site)
        if session and session.get('status') == 'active':
            return session, None  # 已有进行中的会话，直接返回其状态
        active_count = len([s for s in _captures.values() if s.get('status') == 'active'])
        if active_count >= config.MAX_ACTIVE_CAPTURES:
            return None, '进行中的捕获会话已达上限（%d），请先完成或取消' % config.MAX_ACTIVE_CAPTURES
        session = {
            'site': site,
            'login_url': login_url,
            'channel': channel,
            'status': 'active',
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'current_url': '',
            'error': '',
            'cmd': None,
        }
        _captures[site] = session
    threading.Thread(target=capture_worker,
                     args=(site, login_url, channel, session),
                     daemon=True).start()
    return session, None


def get_capture(site):
    """查询捕获会话（不存在返回 None；'cmd' 内部字段不外泄）"""
    with LOCK:
        session = _captures.get(site)
        if not session:
            return None
        return {k: v for k, v in session.items() if k != 'cmd'}


def list_captures():
    with LOCK:
        return [{k: v for k, v in s.items() if k != 'cmd'} for s in _captures.values()]


def capture_action(site, action):
    """向进行中的捕获会话发送指令（complete / cancel）"""
    with LOCK:
        session = _captures.get(site)
        if not session or session.get('status') != 'active':
            return False
        session['cmd'] = 'complete' if action == 'complete' else 'cancel'
    return True


# ==================== 用凭据直接打开网站 ====================

def open_site_worker(site, session):
    """用已保存的凭据直接打开网站（已登录状态）。浏览器窗口由用户自行关闭，
    关闭后本线程自动清理 Playwright 资源。"""
    pw = None
    browser = None
    try:
        from playwright.sync_api import sync_playwright
        cred = store.get_credential_copy(site)
        if not cred:
            with LOCK:
                session['status'] = 'failed'
                session['error'] = '凭据不存在'
            return
        pw = sync_playwright().start()
        browser = pw.chromium.launch(channel='chrome', headless=False)
        ua = cred.get('user_agent') or ''
        kwargs = {'user_agent': ua} if ua else {}
        target = cred.get('login_url') or ('https://%s' % site)
        state = cred.get('storage_state')
        if state:
            # 完整会话注入（登录捕获保存的）
            context = browser.new_context(storage_state=state, **kwargs)
        else:
            context = browser.new_context(**kwargs)
            # 手填 Cookie 串模式：解析后注入
            raw_cookies = cred.get('cookies') or ''
            try:
                domain = urllib.parse.urlparse(target).hostname or site.split(':', 1)[0]
            except Exception:
                domain = site.split(':', 1)[0]
            parsed = []
            for part in raw_cookies.split(';'):
                part = part.strip()
                if '=' in part:
                    name, _, value = part.partition('=')
                    parsed.append({'name': name.strip(), 'value': value.strip(),
                                   'domain': domain, 'path': '/'})
            if parsed:
                context.add_cookies(parsed)
        page = context.new_page()
        try:
            page.goto(target, wait_until='domcontentloaded', timeout=60000)
        except Exception:
            pass  # 打开慢不致命，用户可手动刷新
        with LOCK:
            session['status'] = 'opened'
        # 等待用户关闭浏览器窗口（或被重新打开指令中断），随后自动清理
        while browser.is_connected():
            with LOCK:
                cmd = session.get('cmd')
            if cmd == 'close':
                break
            time.sleep(0.8)
        with LOCK:
            session['status'] = 'closed'
    except ImportError as e:
        with LOCK:
            session['status'] = 'failed'
            session['error'] = 'playwright 不可用(%s)，请先在管理页一键安装' % str(e)
    except Exception as e:
        msg = str(e)
        hint = '（未找到 Chrome，请安装 Chrome 后重试）' if "Executable doesn't exist" in msg else ''
        with LOCK:
            session['status'] = 'failed'
            session['error'] = ('打开失败%s: %s' % (hint, msg))[:300]
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        with LOCK:
            session['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def start_open(site):
    """用已保存凭据打开网站。返回 (status, session/error)。

    每次调用都会启动一个新的浏览器窗口；若上一会话的浏览器仍开着，
    先通过 cmd 标志通知旧 worker 关闭再启动新的。
    """
    if not store.has_credentials(site):
        return 'missing', None
    with LOCK:
        old = _opens.get(site)
        if old and old.get('status') in ('opened', 'opening'):
            old['cmd'] = 'close'  # 通知旧 worker 关闭浏览器
        session = {
            'site': site,
            'status': 'opening',
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'error': '',
            'cmd': None,
        }
        _opens[site] = session
    threading.Thread(target=open_site_worker, args=(site, session), daemon=True).start()
    return 'opening', session


def get_open(site):
    with LOCK:
        session = _opens.get(site)
        if not session:
            return None
        return {k: v for k, v in session.items() if k != 'cmd'}
