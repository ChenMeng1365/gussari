# -*- coding: utf-8 -*-
"""
gussari API 集成测试用例

运行方式: python test/test_api.py

不依赖外部测试框架，自带简易断言。
以子进程启动 server.py（随机端口 + 临时数据目录），用 urllib 完成全部
HTTP 端点验证后终止进程，不触碰项目 data/ 下的真实凭据。
浏览器捕获/打开网站/一键安装等会真实弹窗的能力不在 HTTP 层测试。
"""
import os
import sys
import json
import time
import socket
import shutil
import tempfile
import threading
import subprocess
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server.py")

passed = 0
failed = 0
failures = []


def assert_(condition, message):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        failures.append(message)
        print("  FAIL: " + message)


def assert_equal(actual, expected, message=""):
    ok = actual == expected
    if not ok:
        message = (message or "") + " (期望: %r, 实际: %r)" % (expected, actual)
    assert_(ok, message)


def section(name):
    print("")
    print("-- " + name + " --")


def free_port():
    """随机选一个可绑定端口。

    不用 bind(0)（动态端口段，Windows 上易与外连源端口撞车），
    而是从静态区间随机取并做绑定探测。
    """
    import random
    for _ in range(20):
        port = random.randint(25000, 29999)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            s.close()
            continue
    raise RuntimeError("未能找到可用端口")


# 禁用系统代理的 opener（本地 127.0.0.1 测试请求不应被代理工具劫持返回 502）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def req(method, url, body=None, timeout=15, headers=None):
    """发请求，返回 (状态码, 解析后的 JSON 或原始文本)"""
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with _OPENER.open(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        code = e.code
    try:
        return code, json.loads(raw)
    except Exception:
        return code, raw


def start_server(extra_args=None):
    """启动被测服务（端口被意外抢占时自动换端口重试）"""
    for attempt in range(3):
        port = free_port()
        data_dir = tempfile.mkdtemp(prefix="gussari_apitest_")
        env = os.environ.copy()
        env["GUSSARI_DATA_DIR"] = data_dir          # 临时数据目录，隔离真实凭据
        env["GUSSARI_AUTO_OPEN"] = "0"               # 禁止测试中弹浏览器
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [sys.executable, SERVER, "--host", "127.0.0.1", "--port", str(port)]
        cmd += (extra_args or [])
        p = subprocess.Popen(cmd, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = "http://127.0.0.1:%d" % port
        for _ in range(50):
            if p.poll() is not None:
                break
            try:
                code, _ = req("GET", base + "/api/sites", timeout=3)
                # --token 模式下未带令牌返回 401，服务有响应即视为就绪
                if code in (200, 401):
                    return p, base, data_dir
            except Exception:
                time.sleep(0.2)
        # 启动失败：清理后换端口重试
        try:
            p.kill()
            p.wait()
        except Exception:
            pass
        shutil.rmtree(data_dir, ignore_errors=True)
    raise RuntimeError("服务启动失败")


def cleanup(p, data_dir):
    p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
    shutil.rmtree(data_dir, ignore_errors=True)


# ============================================================
# 用例
# ============================================================

def test_main_service():
    p, base, data_dir = start_server()
    try:
        section("管理页")
        code, body = req("GET", base + "/")
        assert_equal(code, 200, "GET / 状态码")
        html = body if isinstance(body, str) else str(body)
        assert_("AI 重放师" in html, "管理页应包含标题")
        assert_("凭据管理" in html, "管理页应包含凭据管理标签")

        code, _ = req("GET", base + "/favicon.ico")
        assert_equal(code, 204, "GET /favicon.ico 状态码")

        section("站点列表（初始为空）")
        code, body = req("GET", base + "/api/sites")
        assert_equal(code, 200, "GET /api/sites 状态码")
        assert_equal(body.get("count"), 0, "初始站点数应为 0")

        section("保存凭据")
        code, body = req("POST", base + "/api/credentials/test.com", {
            "username": "alice", "password": "secret123",
            "cookies": "session=abc; uid=1", "token": "tok-xyz",
            "headers": {"X-Api-Key": "k1"}, "login_url": "test.com/login",
            "notes": "集成测试"})
        assert_equal(code, 200, "POST 保存凭据状态码")
        assert_equal(body.get("status"), "saved", "保存应返回 saved")

        section("获取凭据")
        code, body = req("GET", base + "/api/credentials/test.com")
        assert_equal(code, 200, "GET 凭据状态码")
        assert_equal(body.get("status"), "found", "凭据应存在")
        data = body.get("data", {})
        assert_equal(data.get("username"), "alice", "用户名应一致")
        assert_equal(data.get("cookies"), "session=abc; uid=1", "Cookie 应一致")
        assert_equal(data.get("token"), "tok-xyz", "Token 应一致")
        assert_equal(data.get("login_url"), "https://test.com/login", "登录 URL 应自动补全 https")
        # 原版快照语义：返回自增前的 use_count（对齐单文件版行为）
        assert_equal(data.get("use_count"), 0, "首次获取返回自增前快照 use_count=0")

        code, body = req("GET", base + "/api/credentials/test.com")
        assert_equal(body.get("data", {}).get("use_count"), 1, "第二次获取应返回 use_count=1")

        section("凭据状态")
        code, body = req("GET", base + "/api/status/test.com")
        assert_equal(code, 200, "GET 状态码")
        assert_equal(body.get("has_credentials"), True, "应已有凭据")
        assert_equal(body.get("has_password"), True, "应有密码")
        assert_equal(body.get("has_token"), True, "应有 Token")
        assert_equal(body.get("username"), "alice", "状态中用户名一致")

        code, body = req("GET", base + "/api/status/unknown.com")
        assert_equal(body.get("has_credentials"), False, "未知站点应无凭据")

        section("不存在的凭据")
        code, body = req("GET", base + "/api/credentials/missing.com")
        assert_equal(code, 200, "GET 缺失凭据状态码")
        assert_equal(body.get("status"), "not_found", "缺失凭据应返回 not_found")

        section("登录请求流程")
        code, body = req("POST", base + "/api/request-login/pending.com", {
            "login_url": "https://pending.com/login", "reason": "测试登录请求"})
        assert_equal(code, 200, "发起登录请求状态码")
        req_id = body.get("request_id")
        assert_(bool(req_id), "应返回 request_id")
        assert_equal(body.get("status"), "pending", "登录请求应为 pending")

        code, body = req("GET", base + "/api/pending")
        assert_equal(code, 200, "GET /api/pending 状态码")
        assert_(body.get("count", 0) >= 1, "应有至少 1 条待处理请求")
        assert_(any(r.get("site") == "pending.com" for r in body.get("pending", [])),
                "待处理列表应包含 pending.com")

        code, body = req("POST", base + "/api/complete-login/" + req_id, {
            "username": "bob", "password": "pw", "cookies": "s=2"})
        assert_equal(code, 200, "完成登录请求状态码")
        assert_equal(body.get("status"), "completed", "请求应完成")
        assert_equal(body.get("site"), "pending.com", "完成站点应一致")

        code, body = req("GET", base + "/api/credentials/pending.com")
        assert_equal(body.get("status"), "found", "完成后凭据应可获取")
        assert_equal(body.get("data", {}).get("username"), "bob", "完成凭据用户名")

        code, body = req("GET", base + "/api/pending")
        assert_equal(body.get("count", 1), 0, "完成后待处理应为 0")

        section("长轮询唤醒")
        holder = {}

        def waiter():
            holder["r"] = req("GET", base + "/api/credentials/waiter.com?wait=true", timeout=25)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(1.2)  # 确保等待者已在服务端注册
        code, body = req("POST", base + "/api/credentials/waiter.com", {
            "username": "carol", "cookies": "w=1"})
        assert_equal(code, 200, "保存 waiter.com 凭据状态码")
        t.join(15)
        assert_(not t.is_alive(), "长轮询线程应及时返回")
        wcode, wbody = holder.get("r", (None, None))
        assert_equal(wcode, 200, "长轮询响应状态码")
        assert_equal((wbody or {}).get("status"), "found", "长轮询应被保存动作唤醒并返回 found")
        assert_equal((wbody or {}).get("data", {}).get("username"), "carol",
                     "长轮询返回的凭据应为新保存的")

        section("使用结果上报")
        code, body = req("POST", base + "/api/report/test.com", {
            "success": True, "message": "HTTP 200"})
        assert_equal(code, 200, "上报状态码")
        assert_equal(body.get("valid"), True, "上报应生效")

        code, body = req("POST", base + "/api/report/nocred.com", {"success": True})
        assert_equal(code, 404, "上报不存在凭据应 404")

        section("删除凭据")
        code, body = req("DELETE", base + "/api/credentials/test.com")
        assert_equal(code, 200, "删除状态码")
        assert_equal(body.get("status"), "deleted", "应返回 deleted")
        # 删除后独立文件也应删除
        test_site_file = os.path.join(data_dir, "test.com.json")
        assert_(not os.path.exists(test_site_file),
                "删除后独立文件 test.com.json 应被删除")

        code, body = req("DELETE", base + "/api/credentials/test.com")
        assert_equal(code, 404, "重复删除应 404")

        code, body = req("GET", base + "/api/credentials/test.com")
        assert_equal(body.get("status"), "not_found", "删除后应 not_found")

        section("API 文档")
        code, body = req("GET", base + "/api/docs")
        assert_equal(code, 200, "GET /api/docs 状态码")
        assert_(len(body.get("endpoints", [])) >= 10, "文档应包含足够端点")
        assert_(base in str(body.get("base_url", "")), "文档 base_url 应含实际地址")

        section("依赖探测与捕获会话（只读接口）")
        code, body = req("GET", base + "/api/dependency")
        assert_equal(code, 200, "GET /api/dependency 状态码")
        assert_("playwright" in body, "依赖探测应含 playwright 字段")
        assert_("python" in body, "依赖探测应含 python 字段")

        code, body = req("GET", base + "/api/capture-login/list")
        assert_equal(code, 200, "GET /api/capture-login/list 状态码")
        assert_equal(body.get("count", -1), 0, "初始捕获会话应为 0")

        code, body = req("GET", base + "/api/capture-login/nosite.com")
        assert_equal(code, 200, "查询不存在捕获会话状态码")
        assert_equal(body.get("status"), "missing", "不存在会话应返回 missing")

        code, body = req("POST", base + "/api/capture-login/nosite.com/complete")
        assert_equal(code, 404, "对不存在会话发指令应 404")

        code, body = req("POST", base + "/api/open-site/nocred.com")
        assert_equal(code, 404, "用不存在凭据打开网站应 404")

        code, body = req("GET", base + "/api/open-site/nocred.com")
        assert_equal(body.get("status"), "missing", "不存在打开会话应 missing")

        section("404 与未知路由")
        code, body = req("GET", base + "/api/nonexistent")
        assert_equal(code, 404, "未知 API 应 404")
        code, body = req("POST", base + "/api/nonexistent", {})
        assert_equal(code, 404, "未知 POST API 应 404")

        section("数据持久化与双文件存储")
        # 汇总文件 _credentials.json
        cred_file = os.path.join(data_dir, "_credentials.json")
        assert_(os.path.exists(cred_file), "汇总文件 _credentials.json 应存在")
        with open(cred_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert_("pending.com" in saved.get("credentials", {}), "凭据应已持久化到汇总 JSON")

        # 单站点文件（每个站点一个独立 .json）
        for site_name in ("pending.com", "waiter.com"):
            site_file = os.path.join(data_dir, site_name + ".json")
            assert_(os.path.exists(site_file),
                    "站点 %s 的独立文件应存在" % site_name)
            with open(site_file, "r", encoding="utf-8") as f:
                site_data = json.load(f)
            # 独立文件与汇总文件中该站点的凭据应一致（凭据数据部分）
            agg_data = saved["credentials"][site_name]
            assert_equal(site_data.get("username"), agg_data.get("username"),
                        "站点 %s 独立文件用户名应与汇总文件一致" % site_name)
            assert_equal(site_data.get("cookies"), agg_data.get("cookies"),
                        "站点 %s 独立文件 cookies 应与汇总文件一致" % site_name)

        # 已删除的 test.com 独立文件不应存在
        assert_(not os.path.exists(os.path.join(data_dir, "test.com.json")),
                "已删除的 test.com 不应有独立文件")

        # 删除凭据后，独立文件也应删除
        code, body = req("DELETE", base + "/api/credentials/waiter.com")
        assert_equal(code, 200, "删除 waiter.com 状态码")
        waiter_file = os.path.join(data_dir, "waiter.com.json")
        assert_(not os.path.exists(waiter_file),
                "删除后独立文件 waiter.com.json 应被删除")
        with open(cred_file, "r", encoding="utf-8") as f:
            saved2 = json.load(f)
        assert_("waiter.com" not in saved2.get("credentials", {}),
                "删除后汇总文件中也不应包含 waiter.com")
    finally:
        cleanup(p, data_dir)


def test_token_auth():
    section("Token 鉴权模式")
    p, base, data_dir = start_server(["--token", "s3cret"])
    try:
        code, _ = req("GET", base + "/api/sites")
        assert_equal(code, 401, "无 token 应 401")

        code, _ = req("GET", base + "/api/sites",
                      headers={"Authorization": "Bearer wrong"})
        assert_equal(code, 401, "错 token 应 401")

        code, body = req("GET", base + "/api/sites",
                         headers={"Authorization": "Bearer s3cret"})
        assert_equal(code, 200, "正确 Bearer 应 200")

        code, body = req("GET", base + "/api/sites?token=s3cret")
        assert_equal(code, 200, "?token= 查询参数应 200")

        code, body = req("GET", base + "/")
        assert_equal(code, 200, "管理页不受 token 保护")

        code, body = req("POST", base + "/api/credentials/auth.com",
                         {"username": "x"},
                         headers={"Authorization": "Bearer s3cret"})
        assert_equal(code, 200, "带 token 保存应成功")

        code, body = req("GET", base + "/api/credentials/auth.com",
                         headers={"Authorization": "Bearer s3cret"})
        assert_equal(body.get("status"), "found", "带 token 读取应成功")
    finally:
        cleanup(p, data_dir)


def test_migration():
    section("旧文件迁移（credentials.json → _credentials.json + 单站点文件）")
    data_dir = tempfile.mkdtemp(prefix="gussari_migrate_")
    # 在临时目录中创建旧格式 credentials.json
    old_file = os.path.join(data_dir, "credentials.json")
    old_data = {
        "credentials": {
            "migrate.com": {
                "username": "migrator",
                "password": "pw123",
                "cookies": "session=abc",
                "token": "",
                "headers": {},
                "login_url": "https://migrate.com/login",
                "notes": "migration test",
                "storage_state": None,
                "user_agent": "TestUA",
                "created_at": "2026-01-01 00:00:00",
                "updated_at": "2026-01-01 00:00:00",
                "use_count": 0,
                "last_used": None,
                "last_result": None,
                "last_result_message": None,
                "last_result_time": None,
            }
        },
        "pending": {}
    }
    with open(old_file, "w", encoding="utf-8") as f:
        json.dump(old_data, f, ensure_ascii=False, indent=2)

    p, base, _ = start_server_with_dir(data_dir)
    try:
        # 服务启动后应自动迁移
        new_file = os.path.join(data_dir, "_credentials.json")
        assert_(os.path.exists(new_file), "迁移后应生成 _credentials.json")
        site_file = os.path.join(data_dir, "migrate.com.json")
        assert_(os.path.exists(site_file), "迁移后应生成单站点文件 migrate.com.json")

        # 验证迁移后凭据可用
        code, body = req("GET", base + "/api/credentials/migrate.com")
        assert_equal(code, 200, "迁移后获取凭据状态码")
        assert_equal(body.get("status"), "found", "迁移后凭据应可获取")
        assert_equal(body.get("data", {}).get("username"), "migrator",
                     "迁移后用户名应一致")
        assert_equal(body.get("data", {}).get("cookies"), "session=abc",
                     "迁移后 cookies 应一致")

        # 旧文件可保留（不主动删除，用户自行清理）
        assert_(os.path.exists(old_file), "旧文件 credentials.json 可保留")
    finally:
        cleanup(p, data_dir)


def test_rebuild_from_site_files():
    section("从单站点文件重建（汇总文件丢失）")
    data_dir = tempfile.mkdtemp(prefix="gussari_rebuild_")
    # 只创建单站点文件，不创建汇总文件
    site_file = os.path.join(data_dir, "rebuild.com.json")
    site_data = {
        "username": "rebuilder",
        "password": "rb456",
        "cookies": "token=xyz",
        "token": "",
        "headers": {},
        "login_url": "https://rebuild.com",
        "notes": "rebuild test",
        "storage_state": None,
        "user_agent": "",
        "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
        "use_count": 0,
        "last_used": None,
        "last_result": None,
        "last_result_message": None,
        "last_result_time": None,
    }
    with open(site_file, "w", encoding="utf-8") as f:
        json.dump(site_data, f, ensure_ascii=False, indent=2)

    p, base, _ = start_server_with_dir(data_dir)
    try:
        # 服务启动后应从单站点文件重建并生成汇总文件
        new_file = os.path.join(data_dir, "_credentials.json")
        assert_(os.path.exists(new_file), "重建后应生成汇总文件 _credentials.json")

        code, body = req("GET", base + "/api/credentials/rebuild.com")
        assert_equal(code, 200, "重建后获取凭据状态码")
        assert_equal(body.get("status"), "found", "重建后凭据应可获取")
        assert_equal(body.get("data", {}).get("username"), "rebuilder",
                     "重建后用户名应一致")
        assert_equal(body.get("data", {}).get("cookies"), "token=xyz",
                     "重建后 cookies 应一致")
    finally:
        cleanup(p, data_dir)


def start_server_with_dir(data_dir, extra_args=None):
    """启动被测服务并指定数据目录（不使用随机目录，用于迁移/重建测试）"""
    for attempt in range(3):
        port = free_port()
        env = os.environ.copy()
        env["GUSSARI_DATA_DIR"] = data_dir
        env["GUSSARI_AUTO_OPEN"] = "0"
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [sys.executable, SERVER, "--host", "127.0.0.1", "--port", str(port)]
        cmd += (extra_args or [])
        p = subprocess.Popen(cmd, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        base = "http://127.0.0.1:%d" % port
        for _ in range(50):
            if p.poll() is not None:
                break
            try:
                code, _ = req("GET", base + "/api/sites", timeout=3)
                if code in (200, 401):
                    return p, base, data_dir
            except Exception:
                time.sleep(0.2)
        try:
            p.kill()
            p.wait()
        except Exception:
            pass
    raise RuntimeError("服务启动失败")


def test_script_management():
    """测试脚本管理 API（不需要浏览器，直接操作文件系统）"""
    section("脚本管理 API")
    p, base, data_dir = start_server()
    try:
        # 1. 初始脚本列表为空
        code, body = req("GET", base + "/api/scripts/test.com")
        assert_equal(code, 200, "获取脚本列表状态码")
        assert_equal(body.get("count"), 0, "初始脚本数应为 0")

        # 2. 通过 recorder 模块直接写入测试脚本
        sys.path.insert(0, os.path.join(ROOT, "src"))
        import config as _cfg
        import recorder as _rec
        _cfg.DATA_DIR = data_dir
        _cfg.SCRIPTS_DIR = os.path.join(data_dir, "scripts")

        # 保存脚本 1（前置）
        script1 = {
            "name": "login_check", "label": "登录验证", "site": "test.com",
            "type": "pre", "description": "验证登录状态",
            "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-01 00:00:00",
            "actions": [
                {"type": "goto", "url": "https://test.com/home"},
                {"type": "wait", "selector": ".user-avatar"},
            ],
        }
        _rec.save_script("test.com", "login_check", script1)

        # 保存脚本 2（主操作）
        script2 = {
            "name": "view_orders", "label": "查看订单", "site": "test.com",
            "type": "main", "description": "导航到订单页面",
            "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-01 00:00:00",
            "actions": [
                {"type": "click", "selector": "#orders-link"},
                {"type": "wait", "selector": ".order-list"},
                {"type": "screenshot"},
            ],
        }
        _rec.save_script("test.com", "view_orders", script2)

        # 保存脚本 3（后置）
        script3 = {
            "name": "logout", "label": "退出登录", "site": "test.com",
            "type": "post", "description": "清理会话",
            "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-01 00:00:00",
            "actions": [
                {"type": "click", "selector": "#logout-btn"},
            ],
        }
        _rec.save_script("test.com", "logout", script3)

        # 3. 验证脚本列表
        code, body = req("GET", base + "/api/scripts/test.com")
        assert_equal(code, 200, "获取脚本列表状态码")
        assert_equal(body.get("count"), 3, "应有 3 个脚本")
        names = [s["name"] for s in body.get("scripts", [])]
        assert_("login_check" in names, "列表应包含 login_check")
        assert_("view_orders" in names, "列表应包含 view_orders")
        assert_("logout" in names, "列表应包含 logout")

        # 4. 验证单个脚本详情
        code, body = req("GET", base + "/api/scripts/test.com/view_orders")
        assert_equal(code, 200, "获取脚本详情状态码")
        assert_equal(body.get("name"), "view_orders", "脚本名应一致")
        assert_equal(body.get("type"), "main", "脚本类型应一致")
        assert_equal(len(body.get("actions", [])), 3, "动作数应为 3")

        # 5. 验证不存在的脚本
        code, body = req("GET", base + "/api/scripts/test.com/nonexistent")
        assert_equal(code, 404, "不存在脚本应 404")

        # 6. 删除脚本
        code, body = req("DELETE", base + "/api/scripts/test.com/logout")
        assert_equal(code, 200, "删除脚本状态码")
        assert_equal(body.get("status"), "deleted", "应返回 deleted")

        code, body = req("GET", base + "/api/scripts/test.com")
        assert_equal(body.get("count"), 2, "删除后应剩 2 个脚本")

        # 7. 重复删除
        code, body = req("DELETE", base + "/api/scripts/test.com/logout")
        assert_equal(code, 404, "重复删除应 404")

        # 8. 录制会话查询（无浏览器弹窗的只读接口）
        code, body = req("GET", base + "/api/record/test.com")
        assert_equal(code, 200, "查询录制会话状态码")
        assert_equal(body.get("status"), "missing", "无录制会话应返回 missing")

        code, body = req("GET", base + "/api/replays/list")
        assert_equal(code, 200, "GET /api/replays/list 状态码")

        code, body = req("GET", base + "/api/replay/test.com")
        assert_equal(code, 200, "查询回放会话状态码")
        assert_equal(body.get("status"), "missing", "无回放会话应返回 missing")

        # 9. 回放无凭据站点
        code, body = req("POST", base + "/api/replay/test.com", {
            "main": "view_orders", "pre": [], "post": []})
        assert_equal(code, 404, "无凭据站点回放应 404")
    finally:
        cleanup(p, data_dir)


def test_exec_scripts():
    """测试 exec 类型脚本（外部命令脚本）的创建和管理"""
    section("Exec 类型脚本")
    p, base, data_dir = start_server()
    try:
        sys.path.insert(0, os.path.join(ROOT, "src"))
        import config as _cfg
        import recorder as _rec
        _cfg.DATA_DIR = data_dir
        _cfg.SCRIPTS_DIR = os.path.join(data_dir, "scripts")

        # 1. 通过 API 创建 exec 脚本（前置）
        code, body = req("POST", base + "/api/scripts/test.com/init_db", {
            "engine": "exec", "label": "初始化数据库",
            "type": "pre", "command": "echo hello",
            "cwd": "", "timeout": 30, "description": "初始化测试数据"
        })
        assert_equal(code, 200, "创建 exec 脚本状态码")
        assert_equal(body.get("status"), "created", "应返回 created")
        assert_equal(body.get("engine"), "exec", "engine 应为 exec")

        # 2. 验证脚本出现在列表中
        code, body = req("GET", base + "/api/scripts/test.com")
        assert_equal(code, 200, "获取脚本列表状态码")
        assert_equal(body.get("count"), 1, "应有 1 个脚本")
        s = body["scripts"][0]
        assert_equal(s.get("engine"), "exec", "脚本 engine 应为 exec")
        assert_equal(s.get("command"), "echo hello", "command 应一致")
        assert_equal(s.get("type"), "pre", "type 应为 pre")

        # 3. 验证脚本详情
        code, body = req("GET", base + "/api/scripts/test.com/init_db")
        assert_equal(code, 200, "获取脚本详情状态码")
        assert_equal(body.get("engine"), "exec", "详情 engine 应为 exec")
        assert_equal(body.get("command"), "echo hello", "详情 command 应一致")
        assert_equal(body.get("timeout"), 30, "超时应为 30")

        # 4. 更新 exec 脚本
        code, body = req("POST", base + "/api/scripts/test.com/init_db", {
            "engine": "exec", "command": "echo updated",
            "label": "初始化数据库V2", "timeout": 60
        })
        assert_equal(code, 200, "更新 exec 脚本状态码")
        code, body = req("GET", base + "/api/scripts/test.com/init_db")
        assert_equal(body.get("command"), "echo updated", "更新后 command 应一致")
        assert_equal(body.get("label"), "初始化数据库V2", "更新后 label 应一致")
        assert_equal(body.get("timeout"), 60, "更新后 timeout 应为 60")

        # 5. 验证 exec 脚本和 web 脚本可在同一站点共存
        web_script = {
            "name": "view_page", "label": "查看页面", "site": "test.com",
            "engine": "web", "type": "main", "description": "网页操作",
            "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-01 00:00:00",
            "actions": [{"type": "goto", "url": "https://test.com"}],
        }
        _rec.save_script("test.com", "view_page", web_script)
        code, body = req("GET", base + "/api/scripts/test.com")
        assert_equal(body.get("count"), 2, "应有 2 个脚本（1 exec + 1 web）")
        engines = [s.get("engine") for s in body["scripts"]]
        assert_("exec" in engines, "应包含 exec 类型")
        assert_("web" in engines, "应包含 web 类型")

        # 6. 删除 exec 脚本
        code, body = req("DELETE", base + "/api/scripts/test.com/init_db")
        assert_equal(code, 200, "删除 exec 脚本状态码")
        code, body = req("GET", base + "/api/scripts/test.com")
        assert_equal(body.get("count"), 1, "删除后应剩 1 个脚本")
    finally:
        cleanup(p, data_dir)


def main():
    print("=" * 56)
    print("  gussari API 集成测试")
    print("=" * 56)
    t0 = time.time()
    test_main_service()
    test_token_auth()
    test_migration()
    test_rebuild_from_site_files()
    test_script_management()
    test_exec_scripts()
    dt = time.time() - t0
    print("")
    print("=" * 56)
    print("  通过: %d  失败: %d  耗时: %.1fs" % (passed, failed, dt))
    print("=" * 56)
    if failures:
        print("失败明细:")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
