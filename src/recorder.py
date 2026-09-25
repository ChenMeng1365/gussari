# -*- coding: utf-8 -*-
# ============================================================
# recorder.py — Playwright 脚本录制与回放引擎
# ============================================================
# 职责:
#   - 网页脚本录制：打开浏览器（注入已保存凭据），记录用户操作为
#     可回放的动作序列（JSON），保存为按站点组织的脚本文件。
#   - 脚本回放：按 前置 → 主操作 → 后置 顺序执行已录制的动作序列，
#     逐步记录日志和截图，报告成功/失败。
#   - 脚本管理：列出、查看、删除、更新已录制脚本的元数据。
#
# 线程模型:
#   与 capture.py 一致，每个录制/回放会话由专用 daemon 线程运行，
#   主线程经 session dict + store.LOCK 轮询通信。
#
# 脚本存储:
#   data/scripts/{site}/{name}.json    — 单个脚本（含动作序列）
#   同一站点所有脚本放在同一目录，文件名即脚本名。
# ============================================================

import json
import os
import threading
import time
from datetime import datetime

import config
import store

LOCK = store.LOCK  # 共用全局锁

# 会话状态（内存态，不持久化）
_recordings = {}   # {site: {status, login_url, channel, current_url, action_count, cmd, ...}}
_replays = {}      # {site: {status, config, log, current_step, total_steps, current_step_index, cmd, ...}}


# ==================== 录制注入 JS ====================

_RECORD_JS = r"""
(function() {
  if (window.__gussari_recording) return;
  window.__gussari_recording = true;

  // 从 sessionStorage 恢复（跨导航保持）
  var stored;
  try { stored = JSON.parse(sessionStorage.getItem('__gussari_actions') || '[]'); } catch(e) { stored = []; }
  var actions = stored;
  var storedStart = sessionStorage.getItem('__gussari_start');
  var startTs = storedStart ? parseInt(storedStart) : Date.now();
  sessionStorage.setItem('__gussari_start', String(startTs));

  function ts() { return Date.now() - startTs; }
  function save() { try { sessionStorage.setItem('__gussari_actions', JSON.stringify(actions)); } catch(e) {} }
  function add(a) { a.ts = ts(); actions.push(a); save(); }

  function sel(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + el.id;
    var parts = [];
    var cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 5) {
      var p = cur.tagName.toLowerCase();
      if (cur.className && typeof cur.className === 'string') {
        var cs = cur.className.trim().split(/\s+/).filter(function(c) { return c; });
        if (cs.length) p += '.' + cs.slice(0, 2).join('.');
      }
      var pn = cur.parentNode;
      if (pn && pn.children && pn.children.length > 1) {
        var sibs = [];
        for (var i = 0; i < pn.children.length; i++) {
          if (pn.children[i].tagName === cur.tagName) sibs.push(pn.children[i]);
        }
        if (sibs.length > 1) p += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(p);
      cur = pn;
    }
    return parts.join(' > ');
  }

  // 点击
  document.addEventListener('click', function(e) {
    add({type: 'click', selector: sel(e.target)});
  }, true);

  // 表单变更（input / textarea / select / checkbox / radio）
  document.addEventListener('change', function(e) {
    var el = e.target;
    if (el.tagName === 'INPUT') {
      if (el.type === 'checkbox' || el.type === 'radio') {
        add({type: 'check', selector: sel(el), checked: el.checked});
      } else {
        add({type: 'fill', selector: sel(el), value: el.value});
      }
    } else if (el.tagName === 'TEXTAREA') {
      add({type: 'fill', selector: sel(el), value: el.value});
    } else if (el.tagName === 'SELECT') {
      add({type: 'select', selector: sel(el), value: el.value});
    }
  }, true);

  // 按键（Enter / Tab）
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === 'Tab') {
      add({type: 'press', key: e.key, selector: sel(e.target)});
    }
  }, true);

  // SPA URL 变更检测
  var lastUrl = location.href;
  setInterval(function() {
    if (location.href !== lastUrl) {
      add({type: 'goto', url: location.href});
      lastUrl = location.href;
    }
  }, 500);
})();
"""


# ==================== 录制会话 ====================

def recording_worker(site, login_url, channel, session):
    """录制线程：Playwright 的全部操作都在本线程内完成"""
    pw = None
    browser = None
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        launch_kwargs = {'headless': False}
        if channel:
            launch_kwargs['channel'] = channel
        browser = pw.chromium.launch(**launch_kwargs)

        # 注入已保存凭据（如有），使浏览器以已登录状态打开
        cred = store.get_credential_copy(site)
        ua = cred.get('user_agent', '') if cred else ''
        state = cred.get('storage_state') if cred else None
        ctx_kwargs = {}
        if ua:
            ctx_kwargs['user_agent'] = ua
        if state:
            ctx_kwargs['storage_state'] = state
        context = browser.new_context(**ctx_kwargs)

        # 注入录制 JS（每次导航自动重新注入）
        context.add_init_script(_RECORD_JS)

        page = context.new_page()
        try:
            page.goto(login_url, wait_until='domcontentloaded', timeout=60000)
        except Exception:
            pass  # 页面加载慢不致命

        with LOCK:
            session['status'] = 'recording'

        # 等待用户在网页上点「保存」或「取消」
        deadline = time.time() + config.RECORDING_TIMEOUT
        while True:
            with LOCK:
                cmd = session.get('cmd')
            if cmd:
                break
            try:
                url = page.url
                with LOCK:
                    session['current_url'] = url
                # 从 sessionStorage 轮询已录制动作数
                count = page.evaluate('''() => {
                    try { return JSON.parse(sessionStorage.getItem("__gussari_actions") || "[]").length; }
                    catch(e) { return 0; }
                }''')
                with LOCK:
                    session['action_count'] = count
            except Exception:
                pass
            if time.time() > deadline:
                cmd = 'timeout'
                break
            time.sleep(0.8)

        if cmd == 'complete':
            time.sleep(0.5)  # 给最后的异步事件留一点时间
            try:
                actions = page.evaluate('''() => {
                    try { return JSON.parse(sessionStorage.getItem("__gussari_actions") || "[]"); }
                    catch(e) { return []; }
                }''')
            except Exception:
                actions = []
            with LOCK:
                session['status'] = 'completed'
                session['actions'] = actions
        elif cmd == 'cancel':
            with LOCK:
                session['status'] = 'cancelled'
        else:
            with LOCK:
                session['status'] = 'timeout'
                session['error'] = '录制超时（超过 %d 分钟未完成），请重新发起' % (config.RECORDING_TIMEOUT // 60)
    except ImportError as e:
        with LOCK:
            session['status'] = 'failed'
            session['error'] = ('playwright 不可用(%s)。请在凭据管理页点击「一键安装 playwright」' % str(e))[:300]
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


def start_recording(site, login_url, channel):
    """启动一次录制会话。成功返回 (session, None)，失败返回 (None, error)"""
    with LOCK:
        session = _recordings.get(site)
        if session and session.get('status') == 'recording':
            return session, None  # 已有进行中的会话
        active = len([s for s in _recordings.values() if s.get('status') == 'recording'])
        if active >= config.MAX_ACTIVE_RECORDINGS:
            return None, '进行中的录制会话已达上限（%d），请先完成或取消' % config.MAX_ACTIVE_RECORDINGS
        # 自动补全协议
        if login_url and not login_url.startswith(('http://', 'https://')):
            login_url = 'https://' + login_url
        session = {
            'site': site,
            'login_url': login_url,
            'channel': channel,
            'status': 'starting',
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'current_url': '',
            'action_count': 0,
            'error': '',
            'cmd': None,
            'actions': [],
        }
        _recordings[site] = session
    threading.Thread(target=recording_worker,
                     args=(site, login_url, channel, session),
                     daemon=True).start()
    return session, None


def get_recording(site):
    """查询录制会话（不存在返回 None；'cmd'/'actions' 内部字段不外泄）"""
    with LOCK:
        session = _recordings.get(site)
        if not session:
            return None
        return {k: v for k, v in session.items() if k not in ('cmd', 'actions')}


def get_recording_actions(site):
    """获取录制会话的动作列表（仅在 completed 状态有效）"""
    with LOCK:
        session = _recordings.get(site)
        if not session or session.get('status') != 'completed':
            return None
        return list(session.get('actions', []))


def recording_action(site, action):
    """向进行中的录制会话发送指令（complete / cancel）"""
    with LOCK:
        session = _recordings.get(site)
        if not session or session.get('status') not in ('recording', 'starting'):
            return False
        session['cmd'] = 'complete' if action == 'complete' else 'cancel'
    return True


def list_recordings():
    with LOCK:
        return [{k: v for k, v in s.items() if k not in ('cmd', 'actions')}
                for s in _recordings.values()]


# ==================== 回放会话 ====================

def _execute_action(page, action):
    """执行单个网页操作动作，返回 (ok, message)"""
    atype = action.get('type')
    try:
        if atype == 'goto':
            page.goto(action['url'], wait_until='domcontentloaded', timeout=60000)
        elif atype == 'click':
            page.click(action['selector'], timeout=10000)
        elif atype == 'fill':
            page.fill(action['selector'], action['value'], timeout=10000)
        elif atype == 'select':
            page.select_option(action['selector'], action['value'], timeout=10000)
        elif atype == 'check':
            if action.get('checked'):
                page.check(action['selector'], timeout=10000)
            else:
                page.uncheck(action['selector'], timeout=10000)
        elif atype == 'press':
            target = action.get('selector', 'body')
            if target:
                page.press(target, action['key'], timeout=10000)
            else:
                page.keyboard.press(action['key'])
        elif atype == 'wait':
            page.wait_for_selector(action['selector'], timeout=15000)
        else:
            return False, '未知动作类型: %s' % atype
        return True, ''
    except Exception as e:
        return False, str(e)[:200]


def _execute_exec_script(script_data):
    """执行 exec 类型脚本（外部命令），返回 (ok, stdout, stderr)

    script_data 格式:
        {'engine': 'exec', 'command': 'python init.py', 'cwd': 'E:\\project', 'timeout': 60}
    """
    import subprocess as _sp
    command = script_data.get('command', '').strip()
    if not command:
        return False, '', '命令为空'
    cwd = script_data.get('cwd', '').strip() or None
    timeout = script_data.get('timeout', 120)
    try:
        # shell=True 使命令字符串可以包含管道、重定向等
        proc = _sp.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd, errors='replace'
        )
        ok = proc.returncode == 0
        return ok, proc.stdout or '', proc.stderr or ''
    except _sp.TimeoutExpired:
        return False, '', '执行超时（%d 秒）' % timeout
    except Exception as e:
        return False, '', str(e)[:200]


def _needs_browser(script_list, site):
    """检查脚本列表中是否有 web 类型的脚本（需要启动浏览器）"""
    for sname in script_list:
        if not sname:
            continue
        data = get_script(site, sname)
        if data and data.get('engine', 'web') == 'web':
            return True
    return False


def replay_worker(site, replay_config, session):
    """回放线程：按 前置 → 主操作 → 后置 顺序执行脚本。

    每个脚本按其 engine 字段分发：
      - web:  Playwright 浏览器操作（需要已保存凭据）
      - exec: subprocess 执行外部命令
    浏览器仅在有 web 类型脚本时才启动。
    """
    pw = None
    browser = None
    try:
        # 构建完整执行序列
        phases = [
            ('pre', replay_config.get('pre', [])),
            ('main', [replay_config.get('main')] if replay_config.get('main') else []),
            ('post', replay_config.get('post', [])),
        ]
        all_scripts = []
        for _, scripts in phases:
            for s in scripts:
                if s:
                    all_scripts.append(s)

        # 判断是否需要浏览器
        need_browser = _needs_browser(all_scripts, site)

        page = None
        if need_browser:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.launch(channel='chrome', headless=False)

            cred = store.get_credential_copy(site)
            if not cred:
                with LOCK:
                    session['status'] = 'failed'
                    session['error'] = '凭据不存在，请先在凭据管理页保存该站点的登录凭据'
                return
            ua = cred.get('user_agent', '')
            state = cred.get('storage_state')
            ctx_kwargs = {}
            if ua:
                ctx_kwargs['user_agent'] = ua
            if state:
                ctx_kwargs['storage_state'] = state
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()

        with LOCK:
            session['status'] = 'running'

        log = []
        total_scripts = len(all_scripts)
        script_index = 0

        for phase_name, script_names in phases:
            for sname in script_names:
                if not sname:
                    continue
                script_data = get_script(site, sname)
                if not script_data:
                    log.append({
                        'phase': phase_name, 'script': sname, 'step': -1,
                        'engine': 'unknown',
                        'status': 'error', 'message': '脚本不存在: %s' % sname,
                        'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    })
                    with LOCK:
                        session['log'] = list(log)
                    script_index += 1
                    continue

                engine = script_data.get('engine', 'web')

                # ---- exec 类型：执行外部命令 ----
                if engine == 'exec':
                    with LOCK:
                        cmd = session.get('cmd')
                    if cmd == 'cancel':
                        break

                    ok, stdout, stderr = _execute_exec_script(script_data)
                    # 截取输出（避免日志过长）
                    output = (stdout + ('\n' + stderr if stderr else '')).strip()
                    if len(output) > 2000:
                        output = output[:2000] + '\n...(截断)'

                    entry = {
                        'phase': phase_name,
                        'script': sname,
                        'step': 0,
                        'engine': 'exec',
                        'action': {'type': 'exec', 'command': script_data.get('command', '')},
                        'status': 'ok' if ok else 'error',
                        'message': output,
                        'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    }
                    log.append(entry)
                    script_index += 1
                    with LOCK:
                        session['log'] = list(log)
                        session['current_step'] = '%s/%s/exec' % (phase_name, sname)
                        session['total_steps'] = total_scripts
                        session['current_step_index'] = script_index
                    continue

                # ---- web 类型：Playwright 浏览器操作 ----
                actions = script_data.get('actions', [])
                for i, action in enumerate(actions):
                    with LOCK:
                        cmd = session.get('cmd')
                    if cmd == 'cancel':
                        break

                    ok, msg = _execute_action(page, action)

                    # 截图（可选，仅 web 类型有页面可截图）
                    screenshot = None
                    if replay_config.get('screenshots', True) and page:
                        try:
                            screenshot = page.screenshot(type='png')
                            import base64
                            screenshot = base64.b64encode(screenshot).decode('ascii')
                        except Exception:
                            pass

                    entry = {
                        'phase': phase_name,
                        'script': sname,
                        'step': i,
                        'engine': 'web',
                        'action': action,
                        'status': 'ok' if ok else 'error',
                        'message': msg,
                        'screenshot': screenshot,
                        'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    }
                    log.append(entry)
                    script_index += 1
                    with LOCK:
                        session['log'] = list(log)
                        session['current_step'] = '%s/%s/%d' % (phase_name, sname, i)
                        session['total_steps'] = total_scripts
                        session['current_step_index'] = script_index

                    # 动作间短暂等待（给页面响应时间）
                    time.sleep(0.3)

                if session.get('cmd') == 'cancel':
                    break

        has_error = any(e['status'] == 'error' for e in log)
        with LOCK:
            session['status'] = 'completed' if not session.get('cmd') == 'cancel' else 'cancelled'
            session['has_error'] = has_error
    except ImportError as e:
        with LOCK:
            session['status'] = 'failed'
            session['error'] = 'playwright 不可用(%s)，请先在凭据管理页一键安装' % str(e)
    except Exception as e:
        msg = str(e)
        hint = '（未找到 Chrome，请安装 Chrome 后重试）' if "Executable doesn't exist" in msg else ''
        with LOCK:
            session['status'] = 'failed'
            session['error'] = ('回放失败%s: %s' % (hint, msg))[:300]
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


def start_replay(site, replay_config):
    """启动回放会话。replay_config: {pre: [...], main: '...', post: [...], screenshots: bool}"""
    if not store.has_credentials(site):
        return 'missing', None
    with LOCK:
        old = _replays.get(site)
        if old and old.get('status') == 'running':
            old['cmd'] = 'cancel'  # 通知旧会话停止
        active = len([s for s in _replays.values() if s.get('status') == 'running'])
        if active >= config.MAX_ACTIVE_REPLAYS:
            return 'busy', None
        session = {
            'site': site,
            'status': 'starting',
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'config': replay_config,
            'log': [],
            'current_step': '',
            'total_steps': 0,
            'current_step_index': 0,
            'has_error': False,
            'error': '',
            'cmd': None,
        }
        _replays[site] = session
    threading.Thread(target=replay_worker, args=(site, replay_config, session), daemon=True).start()
    return 'starting', session


def get_replay(site):
    """查询回放会话（'cmd' 内部字段不外泄；截图单独截取避免传输过大）"""
    with LOCK:
        session = _replays.get(site)
        if not session:
            return None
        # 返回时去掉截图数据（太大），单独通过 API 获取
        result = {k: v for k, v in session.items() if k not in ('cmd',)}
        # log 中去掉 screenshot 字段
        result['log'] = [{k: v for k, v in entry.items() if k != 'screenshot'}
                         for entry in session.get('log', [])]
        result['log_count'] = len(session.get('log', []))
        return result


def get_replay_screenshot(site, step_index):
    """获取回放日志中指定步骤的截图（base64 PNG）"""
    with LOCK:
        session = _replays.get(site)
        if not session:
            return None
        log = session.get('log', [])
        if 0 <= step_index < len(log):
            return log[step_index].get('screenshot')
        return None


def cancel_replay(site):
    """取消回放"""
    with LOCK:
        session = _replays.get(site)
        if not session or session.get('status') not in ('running', 'starting'):
            return False
        session['cmd'] = 'cancel'
    return True


def list_replays():
    with LOCK:
        return [{k: v for k, v in s.items() if k not in ('cmd',)}
                for s in _replays.values()]


# ==================== 脚本管理 ====================

def list_scripts(site):
    """列出站点的所有脚本（扫描目录，返回元数据列表）"""
    sdir = config.script_dir(site)
    if not os.path.isdir(sdir):
        return []
    scripts = []
    for fname in os.listdir(sdir):
        if fname.startswith('_') or not fname.endswith('.json'):
            continue
        fpath = os.path.join(sdir, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            scripts.append({
                'name': data.get('name', fname[:-5]),
                'site': site,
                'label': data.get('label', ''),
                'type': data.get('type', 'main'),
                'engine': data.get('engine', 'web'),
                'description': data.get('description', ''),
                'action_count': len(data.get('actions', [])),
                'command': data.get('command', ''),
                'created_at': data.get('created_at', ''),
                'updated_at': data.get('updated_at', ''),
            })
        except Exception:
            pass
    scripts.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
    return scripts


def get_script(site, name):
    """获取单个脚本的完整数据"""
    fpath = config.script_file_path(site, name)
    if not os.path.exists(fpath):
        return None
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_script(site, name, data):
    """保存脚本文件"""
    sdir = config.script_dir(site)
    os.makedirs(sdir, exist_ok=True)
    fpath = config.script_file_path(site, name)
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def delete_script(site, name):
    """删除脚本文件"""
    fpath = config.script_file_path(site, name)
    if not os.path.exists(fpath):
        return False
    try:
        os.remove(fpath)
        return True
    except Exception:
        return False


def update_script(site, name, label=None, script_type=None, description=None,
                  command=None, cwd=None, timeout=None):
    """更新脚本元数据（不修改动作序列）"""
    data = get_script(site, name)
    if not data:
        return False
    if label is not None:
        data['label'] = label
    if script_type is not None:
        data['type'] = script_type
    if description is not None:
        data['description'] = description
    # exec 类型专属字段
    if command is not None:
        data['command'] = command
    if cwd is not None:
        data['cwd'] = cwd
    if timeout is not None:
        data['timeout'] = timeout
    data['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_script(site, name, data)
    return True
