# -*- coding: utf-8 -*-
# ============================================================
# config.py — gussari 全局配置层
# ============================================================
# 职责:
#   - 集中管理路径、端口、超时等全部可调参数
#   - 运行期可变状态（PORT / ACCESS_TOKEN）由 server.py 启动时写入
#
# 数据目录可用环境变量 GUSSARI_DATA_DIR 重定向（测试隔离用）。
# 访问令牌优先级: --token 参数 > CRED_SAVER_TOKEN 环境变量 > 无鉴权。
# ============================================================

import os

# ==================== 路径 ====================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根

DATA_DIR = os.environ.get('GUSSARI_DATA_DIR', '').strip() or os.path.join(BASE_DIR, 'data')
if not os.path.isabs(DATA_DIR):
    DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, DATA_DIR))

# 双文件存储：
#   _credentials.json — 汇总文件（所有站点凭据集中保存）
#   {site}.json       — 单站点文件（每个站点一个独立文件）
# 两者内容一致：保存/更新时同步写入，删除时同步删除。
DATA_FILE = os.path.join(DATA_DIR, '_credentials.json')
OLD_DATA_FILE = os.path.join(DATA_DIR, 'credentials.json')  # 旧文件（自动迁移用）

# Windows 文件名非法字符
_FILENAME_INVALID = '<>:"/\\|?*'


def site_file_path(site):
    """单个站点的凭据文件路径（data/{site}.json）

    site 中的 Windows 非法字符替换为下划线（如 192.168.1.1:8080 → 192.168.1.1_8080.json）。
    """
    safe = site
    for ch in _FILENAME_INVALID:
        safe = safe.replace(ch, '_')
    return os.path.join(DATA_DIR, safe + '.json')


def _safe_name(name):
    """将任意字符串转换为安全的文件名（Windows 非法字符 → 下划线）"""
    safe = name
    for ch in _FILENAME_INVALID:
        safe = safe.replace(ch, '_')
    return safe


def script_dir(site):
    """站点的脚本目录（data/scripts/{site}/）"""
    return os.path.join(SCRIPTS_DIR, _safe_name(site))


def script_file_path(site, name):
    """单个脚本文件路径（data/scripts/{site}/{name}.json）"""
    return os.path.join(script_dir(site), _safe_name(name) + '.json')

WEB_DIR = os.path.join(BASE_DIR, 'web')
WEB_INDEX = os.path.join(WEB_DIR, 'index.html')

# ==================== 网络 ====================
HOST = '127.0.0.1'          # 仅本机监听，勿直接暴露网络
DEFAULT_PORT = 3366
PORT = DEFAULT_PORT          # 运行期由 server.py 按参数/环境变量写入

# ==================== 业务常量 ====================
LONG_POLL_TIMEOUT = 120     # 凭据长轮询超时（秒）
MAX_COMPLETED_KEEP = 50     # 已完成登录请求保留数量上限，超出自动清理最旧的
CAPTURE_TIMEOUT = 900       # 网页登录捕获会话超时（秒）
MAX_ACTIVE_CAPTURES = 5     # 同时进行的浏览器捕获会话上限
PIP_MIRROR = 'https://pypi.tuna.tsinghua.edu.cn/simple'

# ==================== 脚本录制与回放 ====================
SCRIPTS_DIR = os.path.join(DATA_DIR, 'scripts')
RECORDING_TIMEOUT = 900       # 录制会话超时（秒）
MAX_ACTIVE_RECORDINGS = 5    # 同时进行的录制会话上限
MAX_ACTIVE_REPLAYS = 5       # 同时进行的回放会话上限

# ==================== 运行期可变状态 ====================
ACCESS_TOKEN = None          # --token / CRED_SAVER_TOKEN 写入；None 表示无鉴权

# 登录请求到达时是否自动弹浏览器提醒人工（后台服务可关闭）
# 环境变量 GUSSARI_AUTO_OPEN=0 关闭（测试与无人值守场景）
AUTO_OPEN_BROWSER = os.environ.get('GUSSARI_AUTO_OPEN', '1').strip() not in ('0', 'false', 'no')


def set_access_token(token):
    """设置访问令牌（空值等效关闭鉴权）"""
    global ACCESS_TOKEN
    ACCESS_TOKEN = (token or '').strip() or None


def init_access_token(cli_token):
    """按优先级初始化访问令牌: cli 参数 > 环境变量 > 无"""
    if cli_token:
        set_access_token(cli_token)
        return
    set_access_token(os.environ.get('CRED_SAVER_TOKEN', ''))


def web_url(fragment=''):
    """管理页 URL（供弹窗提醒、日志输出）"""
    return 'http://%s:%d%s' % (HOST, PORT, fragment)
