# -*- coding: utf-8 -*-
# ============================================================
# api.py — gussari HTTP API 层
# ============================================================
# 职责:
#   - HTTP 路由分发（GET/POST/DELETE/OPTIONS）
#   - 请求鉴权（可选 Bearer token）
#   - 各 /api/* 端点的 HTTP 编解码；业务逻辑委托 store / capture
#
# 路由:
#   GET  /                        → web/index.html 管理单页
#   GET  /api/sites                → 站点列表
#   GET  /api/credentials/{site}   → 获取凭据 (?wait=true 长轮询)
#   POST /api/credentials/{site}   → 保存凭据
#   DEL  /api/credentials/{site}   → 删除凭据
#   POST /api/request-login/{site} → 发起人工登录请求
#   GET  /api/pending              → 待处理登录请求
#   POST /api/complete-login/{id}  → 完成登录请求
#   POST /api/report/{site}        → 报告凭据使用结果
#   GET  /api/status/{site}        → 凭据状态
#   *    /api/capture-login/*      → 浏览器登录捕获会话
#   *    /api/open-site/{site}     → 用凭据打开网站
#   GET  /api/dependency[/install]→ playwright 探测与一键安装
#   GET  /api/docs                → API 文档
# ============================================================

import json
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler

import capture
import config
import docs
import recorder
import store


class GussariHandler(BaseHTTPRequestHandler):

    # ==================== HTTP 基础 ====================

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        # 安全：不开放 CORS（任意网页将可偷取凭据）。
        # 本机管理页与 API 同源，无需跨域；如确需跨域请自行加白名单。
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        try:
            with open(config.WEB_INDEX, 'rb') as f:
                body = f.read()
        except FileNotFoundError:
            self._send_json(500, {'error': '管理页缺失: %s' % config.WEB_INDEX})
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return {}

    def _check_auth(self, query=None):
        """校验访问令牌。未配置 token 时放行（本地使用）。"""
        if not config.ACCESS_TOKEN:
            return True
        auth = self.headers.get('Authorization', '')
        if auth == 'Bearer ' + config.ACCESS_TOKEN:
            return True
        if query and query.get('token', [''])[0] == config.ACCESS_TOKEN:
            return True
        return False

    def _auth_denied(self):
        self._send_json(401, {'error': '未授权', 'message': '请携带 Authorization: Bearer <token> 或 ?token=<token>'})

    def log_message(self, format, *args):
        # 静音请求级日志（对齐 MetalSkeleton 后台运行约定：
        # server.log 仅保留启动横幅，防止长期后台运行时日志无限膨胀；
        # 排查时可在原单文件版找到对应打印实现）
        pass

    # ==================== 路由 ====================

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == '/' or path == '/index.html':
            self._send_html()
        elif path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
        elif path.startswith('/api/'):
            if not self._check_auth(query):
                self._auth_denied()
                return
            if path == '/api/sites':
                self._handle_list_sites()
            elif path == '/api/pending':
                self._handle_list_pending()
            elif path == '/api/docs':
                self._handle_api_docs()
            elif path.startswith('/api/credentials/'):
                site = urllib.parse.unquote(path[len('/api/credentials/'):])
                if not site:
                    self._send_json(400, {'error': '缺少站点标识'})
                    return
                wait = query.get('wait', ['false'])[0].lower() == 'true'
                self._handle_get_credentials(site, wait)
            elif path.startswith('/api/status/'):
                site = urllib.parse.unquote(path[len('/api/status/'):])
                if not site:
                    self._send_json(400, {'error': '缺少站点标识'})
                    return
                self._handle_get_status(site)
            elif path == '/api/dependency':
                self._send_json(200, capture.get_install_state())
            elif path == '/api/recordings/list':
                self._send_json(200, {'recordings': recorder.list_recordings(),
                                       'count': len(recorder.list_recordings())})
            elif path == '/api/replays/list':
                self._send_json(200, {'replays': recorder.list_replays(),
                                       'count': len(recorder.list_replays())})
            elif path.startswith('/api/record/'):
                site = urllib.parse.unquote(path[len('/api/record/'):])
                if not site:
                    self._send_json(400, {'error': '缺少站点标识'})
                    return
                session = recorder.get_recording(site)
                if session is None:
                    self._send_json(200, {'status': 'missing', 'site': site})
                else:
                    self._send_json(200, session)
            elif path.startswith('/api/replay/'):
                rest = urllib.parse.unquote(path[len('/api/replay/'):])
                # /api/replay/{site}/screenshot/{index}
                if '/screenshot/' in rest:
                    parts = rest.split('/screenshot/', 1)
                    site, idx_str = parts[0], parts[1]
                    try:
                        idx = int(idx_str)
                    except ValueError:
                        self._send_json(400, {'error': '截图索引无效'})
                        return
                    screenshot = recorder.get_replay_screenshot(site, idx)
                    if screenshot is None:
                        self._send_json(404, {'error': '截图不存在'})
                        return
                    # 返回 base64 PNG
                    body = screenshot.encode('ascii')
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                site = rest
                if not site:
                    self._send_json(400, {'error': '缺少站点标识'})
                    return
                session = recorder.get_replay(site)
                if session is None:
                    self._send_json(200, {'status': 'missing', 'site': site})
                else:
                    self._send_json(200, session)
            elif path.startswith('/api/scripts/'):
                rest = urllib.parse.unquote(path[len('/api/scripts/'):])
                if '/' in rest:
                    site, sname = rest.split('/', 1)
                else:
                    site, sname = rest, ''
                if not site:
                    self._send_json(400, {'error': '缺少站点标识'})
                    return
                if not sname:
                    self._handle_list_scripts(site)
                else:
                    self._handle_get_script(site, sname)
            elif path.startswith('/api/open-site/'):
                site = urllib.parse.unquote(path[len('/api/open-site/'):])
                if not site:
                    self._send_json(400, {'error': '缺少站点标识'})
                    return
                self._handle_get_open(site)
            elif path == '/api/capture-login/list':
                self._send_json(200, {'captures': capture.list_captures(),
                                       'count': len(capture.list_captures())})
            elif path.startswith('/api/capture-login/'):
                site = urllib.parse.unquote(path[len('/api/capture-login/'):])
                if not site:
                    self._send_json(400, {'error': '缺少站点标识'})
                    return
                session = capture.get_capture(site)
                if session is None:
                    self._send_json(200, {'status': 'missing', 'site': site})
                else:
                    self._send_json(200, session)
            else:
                self._send_json(404, {'error': 'Not Found', 'path': path})
        else:
            self._send_json(404, {'error': 'Not Found', 'path': path})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith('/api/'):
            if not self._check_auth():
                self._auth_denied()
                return

        if path.startswith('/api/credentials/'):
            site = urllib.parse.unquote(path[len('/api/credentials/'):])
            if not site:
                self._send_json(400, {'error': '缺少站点标识'})
                return
            self._handle_save_credentials(site)
        elif path.startswith('/api/request-login/'):
            site = urllib.parse.unquote(path[len('/api/request-login/'):])
            if not site:
                self._send_json(400, {'error': '缺少站点标识'})
                return
            self._handle_request_login(site)
        elif path.startswith('/api/complete-login/'):
            req_id = urllib.parse.unquote(path[len('/api/complete-login/'):])
            if not req_id:
                self._send_json(400, {'error': '缺少请求 ID'})
                return
            self._handle_complete_login(req_id)
        elif path.startswith('/api/report/'):
            site = urllib.parse.unquote(path[len('/api/report/'):])
            if not site:
                self._send_json(400, {'error': '缺少站点标识'})
                return
            self._handle_report(site)
        elif path.startswith('/api/capture-login/'):
            rest = urllib.parse.unquote(path[len('/api/capture-login/'):])
            if rest.endswith('/complete'):
                site, action = rest[:-len('/complete')], 'complete'
            elif rest.endswith('/cancel'):
                site, action = rest[:-len('/cancel')], 'cancel'
            else:
                site, action = rest, 'start'
            if not site:
                self._send_json(400, {'error': '缺少站点标识'})
                return
            if action == 'start':
                self._handle_start_capture(site)
            else:
                self._handle_capture_action(site, action)
        elif path == '/api/dependency/install':
            if not capture.start_pip_install():
                self._send_json(400, {'error': '安装已在进行中，请等待完成'})
                return
            self._send_json(200, {'status': 'started', 'message': '开始安装 playwright（使用清华镜像）'})
        elif path.startswith('/api/record/'):
            rest = urllib.parse.unquote(path[len('/api/record/'):])
            if rest.endswith('/complete'):
                site, action = rest[:-len('/complete')], 'complete'
            elif rest.endswith('/cancel'):
                site, action = rest[:-len('/cancel')], 'cancel'
            else:
                site, action = rest, 'start'
            if not site:
                self._send_json(400, {'error': '缺少站点标识'})
                return
            if action == 'start':
                self._handle_start_recording(site)
            else:
                self._handle_recording_action(site, action)
        elif path.startswith('/api/replay/'):
            rest = urllib.parse.unquote(path[len('/api/replay/'):])
            if rest.endswith('/cancel'):
                site, action = rest[:-len('/cancel')], 'cancel'
            else:
                site, action = rest, 'start'
            if not site:
                self._send_json(400, {'error': '缺少站点标识'})
                return
            if action == 'start':
                self._handle_start_replay(site)
            else:
                self._handle_cancel_replay(site)
        elif path.startswith('/api/scripts/'):
            rest = urllib.parse.unquote(path[len('/api/scripts/'):])
            if '/' in rest:
                site, sname = rest.split('/', 1)
            else:
                site, sname = rest, ''
            if not site or not sname:
                self._send_json(400, {'error': '缺少站点标识或脚本名'})
                return
            self._handle_update_script(site, sname)
        elif path.startswith('/api/open-site/'):
            site = urllib.parse.unquote(path[len('/api/open-site/'):])
            if not site:
                self._send_json(400, {'error': '缺少站点标识'})
                return
            self._handle_open_site(site)
        else:
            self._send_json(404, {'error': 'Not Found', 'path': path})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith('/api/'):
            if not self._check_auth():
                self._auth_denied()
                return

        if path.startswith('/api/credentials/'):
            site = urllib.parse.unquote(path[len('/api/credentials/'):])
            if not site:
                self._send_json(400, {'error': '缺少站点标识'})
                return
            self._handle_delete_credentials(site)
        elif path.startswith('/api/scripts/'):
            rest = urllib.parse.unquote(path[len('/api/scripts/'):])
            if '/' in rest:
                site, sname = rest.split('/', 1)
            else:
                site, sname = rest, ''
            if not site or not sname:
                self._send_json(400, {'error': '缺少站点标识或脚本名'})
                return
            self._handle_delete_script(site, sname)
        else:
            self._send_json(404, {'error': 'Not Found', 'path': path})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    # ==================== API 处理方法 ====================

    def _handle_list_sites(self):
        sites = store.list_sites()
        self._send_json(200, {'sites': sites, 'count': len(sites)})

    def _handle_get_credentials(self, site, wait):
        # 先检查已有凭据
        cred = store.get_credential_copy(site)
        if cred is not None:
            store.mark_used(site)
            store.save_data()
            store.save_site_file(site)
            self._send_json(200, {'status': 'found', 'site': site, 'data': cred})
            return

        if not wait:
            self._send_json(200, {'status': 'not_found', 'site': site,
                                   'message': '凭据不存在，可使用 POST /api/request-login/' + site + ' 发起登录请求'})
            return

        # 长轮询：检查是否已有待处理请求
        if not store.has_pending_request(site):
            store.create_login_request(site, auto=True)

        # 注册等待
        event = store.register_waiter(site)

        # 轮询等待
        start = time.time()
        found = False
        while time.time() - start < config.LONG_POLL_TIMEOUT:
            if event.wait(timeout=1.0):
                break
            if store.has_credentials(site):
                found = True
                break

        # 清理等待者
        store.unregister_waiter(site, event)

        if found or store.has_credentials(site):
            cred = store.get_credential_copy(site)
            store.mark_used(site)
            store.save_data()
            store.save_site_file(site)
            self._send_json(200, {'status': 'found', 'site': site, 'data': cred})
        else:
            self._send_json(200, {'status': 'timeout', 'site': site, 'message': '等待凭据超时，请稍后重试'})

    def _handle_save_credentials(self, site):
        body = self._read_body()
        store.store_credential(site, body)
        self._send_json(200, {'status': 'saved', 'site': site})

    def _handle_delete_credentials(self, site):
        if not store.delete_credentials(site):
            self._send_json(404, {'error': '凭据不存在', 'site': site})
            return
        self._send_json(200, {'status': 'deleted', 'site': site})

    def _handle_request_login(self, site):
        body = self._read_body()
        login_url = body.get('login_url', '')
        reason = body.get('reason', '机器人需要人工登录')
        request_id = store.create_login_request(site, login_url, reason)
        self._send_json(200, {
            'request_id': request_id,
            'status': 'pending',
            'site': site,
            'message': '已发起登录请求，请在管理页完成认证',
            'wait_url': 'http://%s:%d/api/credentials/%s?wait=true' % (
                config.HOST, config.PORT, urllib.parse.quote(site))
        })

    def _handle_list_pending(self):
        pending = store.list_pending()
        self._send_json(200, {'pending': pending, 'count': len(pending)})

    def _handle_complete_login(self, req_id):
        body = self._read_body()
        req = store.get_request(req_id)
        if req is None:
            self._send_json(404, {'error': '登录请求不存在', 'request_id': req_id})
            return
        site = req['site']
        if not body.get('login_url'):
            body['login_url'] = req.get('login_url', '')
        store.store_credential(site, body)
        self._send_json(200, {
            'status': 'completed',
            'site': site,
            'request_id': req_id,
            'message': '凭据已保存，等待中的机器人将自动获取'
        })

    def _handle_report(self, site):
        body = self._read_body()
        success = body.get('success', False)
        message = body.get('message', '')
        if not store.report_result(site, success, message):
            self._send_json(404, {'error': '凭据不存在', 'site': site})
            return
        self._send_json(200, {'status': 'reported', 'site': site, 'valid': success})

    def _handle_get_status(self, site):
        has_pending = store.has_pending_request(site)
        cred = store.get_credential_copy(site)
        if cred is None:
            self._send_json(200, {
                'site': site,
                'has_credentials': False,
                'has_pending': has_pending
            })
            return
        self._send_json(200, {
            'site': site,
            'has_credentials': True,
            'username': cred.get('username', ''),
            'has_password': bool(cred.get('password')),
            'has_cookies': bool(cred.get('cookies')),
            'has_token': bool(cred.get('token')),
            'has_headers': bool(cred.get('headers')),
            'has_storage_state': bool(cred.get('storage_state')),
            'has_pending': has_pending
        })

    def _handle_api_docs(self):
        base = 'http://%s:%d' % (config.HOST, config.PORT)
        self._send_json(200, docs.build_api_docs(base))

    # ==================== 网页登录捕获 ====================

    def _handle_start_capture(self, site):
        body = self._read_body()
        login_url = (body.get('login_url') or '').strip()
        channel = (body.get('channel') or 'chrome').strip() or 'chrome'
        if not login_url:
            self._send_json(400, {'error': '请提供登录页 URL'})
            return
        # 自动补全协议，默认 https
        if not login_url.startswith(('http://', 'https://')):
            login_url = 'https://' + login_url
        session, error = capture.start_capture(site, login_url, channel)
        if error:
            self._send_json(400, {'error': error})
            return
        self._send_json(200, {'status': session.get('status', 'active'), 'site': site,
                              'current_url': session.get('current_url', ''),
                              'message': '浏览器已启动，完成登录后请在网页上点击保存'})

    def _handle_capture_action(self, site, action):
        if not capture.capture_action(site, action):
            self._send_json(404, {'error': '该站点没有进行中的捕获会话', 'site': site})
            return
        self._send_json(200, {'status': 'cmd_sent', 'site': site, 'action': action})

    # ==================== 用凭据直接打开网站 ====================

    def _handle_open_site(self, site):
        status, session = capture.start_open(site)
        if status == 'missing':
            self._send_json(404, {'error': '该站点没有已保存的凭据', 'site': site})
            return
        message = {'opened': '该站点的浏览器窗口已打开',
                   'opening': '正在用已保存的凭据打开网站'}.get(status, '')
        self._send_json(200, {'status': status, 'site': site, 'message': message})

    def _handle_get_open(self, site):
        session = capture.get_open(site)
        if session is None:
            self._send_json(200, {'status': 'missing', 'site': site})
            return
        self._send_json(200, session)

    # ==================== 脚本录制 ====================

    def _handle_start_recording(self, site):
        body = self._read_body()
        login_url = (body.get('login_url') or '').strip()
        channel = (body.get('channel') or 'chrome').strip() or 'chrome'
        if not login_url:
            # 尝试从已保存凭据中取 login_url
            cred = store.get_credential_copy(site)
            if cred and cred.get('login_url'):
                login_url = cred['login_url']
            else:
                self._send_json(400, {'error': '请提供登录页 URL（或先保存该站点的凭据）'})
                return
        session, error = recorder.start_recording(site, login_url, channel)
        if error:
            self._send_json(400, {'error': error})
            return
        self._send_json(200, {'status': session.get('status', 'starting'), 'site': site,
                              'message': '浏览器已启动，操作完成后请点击保存录制'})

    def _handle_recording_action(self, site, action):
        if action == 'complete':
            # 获取请求体中的脚本元数据
            body = self._read_body()
            name = (body.get('name') or '').strip()
            label = (body.get('label') or '').strip()
            script_type = (body.get('type') or 'main').strip()
            description = (body.get('description') or '').strip()
            if not name:
                self._send_json(400, {'error': '请提供脚本名称'})
                return
            # 先发送 complete 指令
            if not recorder.recording_action(site, 'complete'):
                self._send_json(404, {'error': '该站点没有进行中的录制会话', 'site': site})
                return
            # 等待录制线程完成（最多 10 秒）
            for _ in range(20):
                time.sleep(0.5)
                session = recorder.get_recording(site)
                if session and session.get('status') in ('completed', 'cancelled', 'failed', 'timeout'):
                    break
            # 获取录制的动作
            actions = recorder.get_recording_actions(site)
            if actions is None:
                self._send_json(400, {'error': '录制未完成或未捕获到动作'})
                return
            if not actions:
                self._send_json(400, {'error': '未捕获到任何操作，请重新录制'})
                return
            # 保存脚本
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            script_data = {
                'name': name,
                'label': label or name,
                'site': site,
                'type': script_type,
                'description': description,
                'created_at': now,
                'updated_at': now,
                'actions': actions,
            }
            recorder.save_script(site, name, script_data)
            self._send_json(200, {
                'status': 'saved', 'site': site, 'name': name,
                'action_count': len(actions),
                'message': '脚本已保存: %s（%d 个动作）' % (name, len(actions)),
            })
        else:
            if not recorder.recording_action(site, action):
                self._send_json(404, {'error': '该站点没有进行中的录制会话', 'site': site})
                return
            self._send_json(200, {'status': 'cmd_sent', 'site': site, 'action': action})

    # ==================== 脚本管理 ====================

    def _handle_list_scripts(self, site):
        scripts = recorder.list_scripts(site)
        self._send_json(200, {'site': site, 'scripts': scripts, 'count': len(scripts)})

    def _handle_get_script(self, site, name):
        data = recorder.get_script(site, name)
        if data is None:
            self._send_json(404, {'error': '脚本不存在', 'site': site, 'name': name})
            return
        self._send_json(200, data)

    def _handle_delete_script(self, site, name):
        if not recorder.delete_script(site, name):
            self._send_json(404, {'error': '脚本不存在', 'site': site, 'name': name})
            return
        self._send_json(200, {'status': 'deleted', 'site': site, 'name': name})

    def _handle_update_script(self, site, name):
        body = self._read_body()
        engine = body.get('engine', '').strip()

        # 如果指定了 engine=exec 且脚本不存在，则创建新 exec 脚本
        if engine == 'exec':
            existing = recorder.get_script(site, name)
            if existing is None:
                # 创建新的 exec 脚本
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                script_data = {
                    'name': name,
                    'label': body.get('label', name),
                    'site': site,
                    'engine': 'exec',
                    'type': body.get('type', 'pre'),
                    'description': body.get('description', ''),
                    'command': body.get('command', ''),
                    'cwd': body.get('cwd', ''),
                    'timeout': body.get('timeout', 120),
                    'created_at': now,
                    'updated_at': now,
                }
                recorder.save_script(site, name, script_data)
                self._send_json(200, {'status': 'created', 'site': site, 'name': name,
                                       'engine': 'exec'})
                return

        # 更新已有脚本
        if not recorder.update_script(site, name,
                                       label=body.get('label'),
                                       script_type=body.get('type'),
                                       description=body.get('description'),
                                       command=body.get('command'),
                                       cwd=body.get('cwd'),
                                       timeout=body.get('timeout')):
            self._send_json(404, {'error': '脚本不存在', 'site': site, 'name': name})
            return
        self._send_json(200, {'status': 'updated', 'site': site, 'name': name})

    # ==================== 脚本回放 ====================

    def _handle_start_replay(self, site):
        body = self._read_body()
        replay_config = {
            'pre': body.get('pre', []),
            'main': body.get('main', ''),
            'post': body.get('post', []),
            'screenshots': body.get('screenshots', True),
        }
        if not replay_config['main']:
            self._send_json(400, {'error': '请指定主操作脚本（main）'})
            return
        status, session = recorder.start_replay(site, replay_config)
        if status == 'missing':
            self._send_json(404, {'error': '该站点没有已保存的凭据，请先保存凭据', 'site': site})
            return
        if status == 'busy':
            self._send_json(400, {'error': '回放会话已达上限，请先等待或取消现有会话'})
            return
        self._send_json(200, {'status': status, 'site': site, 'message': '正在启动回放...'})

    def _handle_cancel_replay(self, site):
        if not recorder.cancel_replay(site):
            self._send_json(404, {'error': '该站点没有进行中的回放会话', 'site': site})
            return
        self._send_json(200, {'status': 'cancelled', 'site': site})
