# -*- coding: utf-8 -*-
# ============================================================
# 【1/5】接口或调用说明
# ============================================================
# server.py — gussari AI 重放师 Web 服务器（服务入口）
#
# AI 访问网站时的凭据管理：人工登录一次，机器人自动复用
# Cookie / Token / 浏览器会话（Playwright storage_state）。
#
# 完全独立运行，零第三方依赖（仅 Python 标准库）；
# 浏览器登录捕获功能可选安装 playwright（管理页可一键安装）。
#
# 路由与 API 清单见 src/api.py 头部注释，管理页为 web/index.html。
#
# 用法:
#     python server.py                    启动服务 (默认 127.0.0.1:3366)
#     PORT=3400 python server.py          用 PORT 环境变量指定端口 (--port 仍优先)
#     python server.py --port 9000        指定端口
#     python server.py --host 0.0.0.0     监听所有网卡 (局域网可访问)
#     python server.py --token SECRET     启用 Bearer Token 鉴权
#     python server.py --force            端口被占用时先终止旧实例再启动
#     python server.py --check            仅检测端口状态，不启动
#
# 后台运行（推荐，无命令行窗口残留）:
#     双击 start.bat  → 经 start-hidden.vbs 隐藏窗口后台启动，日志写入 server.log
#     双击 stop.bat   → 从 server.log 解析实际端口，按端口精确终止
# ============================================================

import argparse
import io
import os
import socket
import subprocess
import sys
import time
from http.server import ThreadingHTTPServer

# 核心库位于 src/
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

import config
import store
import capture
from api import GussariHandler

# ============================================================
# 【2/5】可变参数配置区
# ============================================================
# 默认取自 src/config.py；此处仅保留启动横幅文案。
# 后台调度约定: PORT 环境变量可覆盖默认端口 (start-hidden.vbs 经此传端口)，
# --port 命令行参数优先级最高。

# 编码修正 (Windows 控制台); line_buffering 保证后台重定向到 server.log 时逐行实时落盘
# （stop.bat 需要从 server.log 解析实际端口，故启动横幅必须及时落盘）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# ============================================================
# 【3/5】静态设定数据
# ============================================================
BANNER_LINES = [
    "gussari — AI 重放师",
    "人工登录一次，AI 自动复用 Cookie / Token / 浏览器会话",
]


# ============================================================
# 【4/5】处理逻辑
# ============================================================

def check_port(port, host=None):
    """检测端口是否已有服务在监听（Windows 下 allow_reuse_address 会允许
    多进程共享绑定而不报错，导致新旧代码混跑，故启动前显式检测）"""
    host = host or config.HOST
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def _find_pid_on_port(port):
    """查找占用指定端口的进程 PID（Windows netstat）"""
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line.upper():
                cols = line.split()
                if len(cols) >= 5:
                    return cols[-1]
    except Exception:
        pass
    return None


def _kill_pid(pid, timeout=5):
    """终止指定 PID 的进程"""
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=timeout)
        time.sleep(0.5)
        return True
    except Exception:
        return False


def _kill_port(port):
    """终止占用指定端口的所有进程，返回是否成功释放"""
    for _ in range(3):
        pid = _find_pid_on_port(port)
        if not pid:
            return True
        _kill_pid(pid)
        time.sleep(0.5)
    return not check_port(port)


def _print_banner():
    print("=" * 56)
    for line in BANNER_LINES:
        print("  " + line)
    print("=" * 56)
    print("  Web 界面: http://%s:%d" % (config.HOST, config.PORT))
    print("  API 文档: http://%s:%d/api/docs" % (config.HOST, config.PORT))
    print("  Python: %s" % sys.executable)
    print("  playwright: %s" % ("已安装" if capture.has_playwright() else "未安装（网页登录捕获需要，管理页可一键安装）"))
    print("  数据文件: %s" % config.DATA_FILE)
    print("  长轮询超时: %d 秒" % config.LONG_POLL_TIMEOUT)
    print("  访问令牌: %s" % ("已启用 (Authorization: Bearer <token>)" if config.ACCESS_TOKEN else "未启用（本地使用，勿暴露网络）"))
    print("=" * 56)
    print("  后台运行中，停止: stop.bat")


def _start_server():
    """启动 HTTP 服务器"""
    store.load_data()
    _print_banner()

    server = ThreadingHTTPServer((config.HOST, config.PORT), GussariHandler)
    print()
    print(">>> 服务已启动: http://%s:%d <<<" % (config.HOST, config.PORT))
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在保存数据...")
        store.save_data()
        print("数据已保存，服务已停止")
        server.server_close()


# ============================================================
# 【5/5】主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="gussari — AI 重放师 Web 服务器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python server.py                  启动服务 (默认 127.0.0.1:3366)
  python server.py --port 3400      指定端口
  python server.py --host 0.0.0.0   监听所有网卡
  python server.py --token SECRET   启用 Bearer Token 鉴权
  python server.py --force          端口被占用时强制重启
  python server.py --check          仅检测端口状态

后台运行: 双击 start.bat (隐藏窗口, 日志 server.log); 停止: stop.bat
        """)
    parser.add_argument("--port", type=int, default=None,
                        help="监听端口 (默认 %d, PORT 环境变量次之)" % config.DEFAULT_PORT)
    parser.add_argument("--host", default=None,
                        help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--token", default=None,
                        help="访问令牌，启用后所有 /api/* 需携带 Authorization: Bearer <token>")
    parser.add_argument("--force", action="store_true",
                        help="强制重启：如端口已占用先终止旧实例")
    parser.add_argument("--check", action="store_true",
                        help="仅检测端口状态，不启动服务")
    args = parser.parse_args()

    # ---- 参数装配: --port > PORT 环境变量 > 默认 ----
    if args.port is not None:
        config.PORT = args.port
    else:
        env_port = os.environ.get("PORT", "").strip()
        if env_port.isdigit():
            config.PORT = int(env_port)
        else:
            config.PORT = config.DEFAULT_PORT
    if args.host:
        config.HOST = args.host

    # ---- 访问令牌: --token > CRED_SAVER_TOKEN 环境变量 > 无鉴权 ----
    config.init_access_token(args.token)

    # ---- 端口检测 / --check / --force ----
    already_running = check_port(config.PORT)

    if already_running:
        pid = _find_pid_on_port(config.PORT)
        pid_str = " (PID: %s)" % pid if pid else ""
        print("[检测] 服务已在运行%s — http://%s:%d" % (pid_str, config.HOST, config.PORT))

        if args.check:
            return
        if args.force:
            print("[重启] --force 模式，正在终止旧实例...")
            if _kill_port(config.PORT):
                print("[重启] 旧实例已终止")
            else:
                print("[重启] 警告: 端口未能释放，可能需要手动处理")
                return
        else:
            print("[退出] 端口已被占用。加 --force 强制重启，或 --port 换端口。")
            return
    else:
        if args.check:
            print("[检测] 端口 %d 未监听，服务未启动。" % config.PORT)
            return
        if args.force:
            print("[启动] --force 模式，端口空闲，直接启动")

    # ---- 正常启动 ----
    _start_server()


if __name__ == "__main__":
    main()
