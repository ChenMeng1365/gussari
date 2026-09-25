# -*- coding: utf-8 -*-
# ============================================================
# docs.py — gussari API 文档数据层
# ============================================================
# 职责: 生成 /api/docs 与管理页「API 文档」标签页的接口清单与示例。
# 纯函数、零状态，base_url 由调用方传入。
# ============================================================


def build_api_docs(base_url):
    """构建 API 文档 dict（base_url 形如 http://127.0.0.1:3366）"""
    return {
        'title': 'gussari 凭据管理 API',
        'version': '1.0',
        'base_url': base_url,
        'endpoints': [
            {'method': 'GET', 'path': '/api/sites', 'description': '列出所有已保存凭据的站点', 'response': '{"sites":["example.com"],"count":1}'},
            {'method': 'GET', 'path': '/api/credentials/{site}', 'description': '获取指定站点的凭据，加 ?wait=true 可长轮询等待（最多120秒）', 'response': '{"status":"found","site":"...","data":{...}}'},
            {'method': 'POST', 'path': '/api/credentials/{site}', 'description': '保存/更新凭据', 'body': '{"username":"...","password":"...","cookies":"...","token":"...","headers":{},"login_url":"...","notes":"..."}', 'response': '{"status":"saved","site":"..."}'},
            {'method': 'DELETE', 'path': '/api/credentials/{site}', 'description': '删除指定站点的凭据', 'response': '{"status":"deleted","site":"..."}'},
            {'method': 'POST', 'path': '/api/request-login/{site}', 'description': '发起人工登录请求，自动弹出管理页提醒', 'body': '{"login_url":"https://...","reason":"需要人工登录"}', 'response': '{"request_id":"...","status":"pending","site":"..."}'},
            {'method': 'GET', 'path': '/api/pending', 'description': '列出所有待处理的登录请求', 'response': '{"pending":[...],"count":N}'},
            {'method': 'POST', 'path': '/api/complete-login/{request_id}', 'description': '完成登录请求并保存凭据', 'body': '{"username":"...","password":"...","cookies":"...","token":"..."}', 'response': '{"status":"completed","site":"...","request_id":"..."}'},
            {'method': 'POST', 'path': '/api/report/{site}', 'description': '报告凭据使用结果（成功/失败）', 'body': '{"success":true,"message":"登录成功"}', 'response': '{"status":"reported","site":"...","valid":true}'},
            {'method': 'GET', 'path': '/api/status/{site}', 'description': '查询凭据状态（是否存在、使用次数、上次结果等）', 'response': '{"site":"...","has_credentials":true,...}'},
            {'method': 'POST', 'path': '/api/capture-login/{site}', 'description': '网页一键发起浏览器登录捕获：服务端打开 Chrome/Edge，人工登录后点网页按钮保存', 'body': '{"login_url":"https://...","channel":"chrome|msedge"}', 'response': '{"status":"active","site":"..."}'},
            {'method': 'GET', 'path': '/api/capture-login/{site}', 'description': '查询捕获会话状态（含当前页面 URL）', 'response': '{"status":"active|completed|failed|missing","current_url":"..."}'},
            {'method': 'POST', 'path': '/api/capture-login/{site}/complete', 'description': '人工登录完成后确认保存捕获的会话状态', 'response': '{"status":"cmd_sent","action":"complete"}'},
            {'method': 'POST', 'path': '/api/capture-login/{site}/cancel', 'description': '取消捕获并关闭浏览器', 'response': '{"status":"cmd_sent","action":"cancel"}'},
            {'method': 'POST', 'path': '/api/open-site/{site}', 'description': '用已保存的凭据直接打开网站（弹浏览器注入登录态）', 'response': '{"status":"opening","site":"..."}'},
            {'method': 'POST', 'path': '/api/record/{site}', 'description': '开始录制脚本（打开浏览器注入凭据，记录用户操作）', 'body': '{"login_url":"https://...","channel":"chrome"}', 'response': '{"status":"starting","site":"..."}'},
            {'method': 'GET', 'path': '/api/record/{site}', 'description': '查询录制会话状态（含当前页面 URL 和已录制动作数）', 'response': '{"status":"recording","current_url":"...","action_count":5}'},
            {'method': 'POST', 'path': '/api/record/{site}/complete', 'description': '完成录制并保存脚本（需指定脚本名称、类型等元数据）', 'body': '{"name":"view_orders","label":"查看订单","type":"main","description":"..."}', 'response': '{"status":"saved","name":"view_orders","action_count":8}'},
            {'method': 'POST', 'path': '/api/record/{site}/cancel', 'description': '取消录制并关闭浏览器', 'response': '{"status":"cmd_sent","action":"cancel"}'},
            {'method': 'GET', 'path': '/api/scripts/{site}', 'description': '列出该站点的所有已录制脚本', 'response': '{"scripts":[...],"count":N}'},
            {'method': 'GET', 'path': '/api/scripts/{site}/{name}', 'description': '获取单个脚本的完整数据（含动作序列）'},
            {'method': 'DELETE', 'path': '/api/scripts/{site}/{name}', 'description': '删除指定脚本'},
            {'method': 'POST', 'path': '/api/scripts/{site}/{name}', 'description': '更新脚本元数据（label/type/description）', 'body': '{"label":"...","type":"pre","description":"..."}'},
            {'method': 'POST', 'path': '/api/replay/{site}', 'description': '开始回放（按前置→主操作→后置顺序执行脚本）', 'body': '{"pre":["script1"],"main":"main_script","post":["script2"],"screenshots":true}', 'response': '{"status":"starting","site":"..."}'},
            {'method': 'GET', 'path': '/api/replay/{site}', 'description': '查询回放状态（含逐步日志、进度）', 'response': '{"status":"running","log":[...],"current_step":"pre/script1/2"}'},
            {'method': 'POST', 'path': '/api/replay/{site}/cancel', 'description': '取消正在进行的回放'},
            {'method': 'GET', 'path': '/api/dependency', 'description': 'playwright 依赖探测与一键安装状态'},
            {'method': 'GET', 'path': '/api/docs', 'description': '获取本 API 文档（JSON）'},
        ],
        'usage_example_python': (
            'import requests\n'
            '\n'
            "BASE = '" + base_url + "'\n"
            '\n'
            '# ---- 方式一：直接获取凭据 ----\n'
            "resp = requests.get(f'{BASE}/api/credentials/example.com')\n"
            'data = resp.json()\n'
            '\n'
            "if data['status'] == 'found':\n"
            "    cred = data['data']\n"
            '    headers = {}\n'
            "    if cred.get('cookies'):\n"
            "        headers['Cookie'] = cred['cookies']\n"
            "    if cred.get('token'):\n"
            "        headers['Authorization'] = f\"Bearer {cred['token']}\"\n"
            "    if cred.get('headers'):\n"
            "        headers.update(cred['headers'])\n"
            '\n'
            "    r = requests.get('https://example.com/api/data', headers=headers)\n"
            '    # 报告结果\n'
            "    requests.post(f'{BASE}/api/report/example.com',\n"
            "        json={'success': r.status_code == 200, 'message': f'Status: {r.status_code}'})\n"
            '\n'
            'else:\n'
            '    # ---- 方式二：无凭据，发起登录请求 + 长轮询等待 ----\n'
            "    requests.post(f'{BASE}/api/request-login/example.com',\n"
            "        json={'login_url': 'https://example.com/login', 'reason': '首次登录需要人工认证'})\n"
            '\n'
            "    resp = requests.get(f'{BASE}/api/credentials/example.com?wait=true', timeout=130)\n"
            '    data = resp.json()\n'
            "    if data['status'] == 'found':\n"
            "        print('凭据已获取:', data['data']['username'])\n"
            '    else:\n'
            "        print('等待超时，请稍后重试')\n"
        ),
        'usage_example_curl': (
            '# 获取凭据\n'
            'curl ' + base_url + '/api/credentials/example.com\n\n'
            '# 长轮询等待凭据\n'
            'curl "' + base_url + '/api/credentials/example.com?wait=true"\n\n'
            '# 发起登录请求\n'
            "curl -X POST " + base_url + "/api/request-login/example.com \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            "  -d '{\"login_url\":\"https://example.com/login\",\"reason\":\"需要人工登录\"}'\n\n"
            '# 保存凭据\n'
            "curl -X POST " + base_url + "/api/credentials/example.com \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            '  -d \'{"username":"admin","password":"xxx","cookies":"session=abc123","token":"eyJ..."}\'\n\n'
            '# 查询状态\n'
            'curl ' + base_url + '/api/status/example.com\n\n'
            '# 报告结果\n'
            "curl -X POST " + base_url + "/api/report/example.com \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            '  -d \'{"success":true,"message":"登录成功"}\'\n'
        )
    }
