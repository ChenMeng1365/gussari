# -*- coding: utf-8 -*-
# ============================================================
# store.py — gussari 数据层（凭据存储 / 登录请求 / 长轮询等待）
# ============================================================
# 职责:
#   - 凭据、登录请求的内存状态与 JSON 持久化（双文件存储）
#   - 长轮询等待队列（凭据就绪后唤醒等待线程）
#   - 凭据的合并写入（HTTP 保存 / 登录请求完成 / 网页捕获共用）
#
# 双文件存储策略:
#   data/_credentials.json  — 汇总文件（所有站点凭据 + 待处理请求集中保存）
#   data/{site}.json       — 单站点文件（每个站点一个独立文件）
#   两者内容保持一致：保存/更新时同步写入两侧，删除时同步删除。
#   加载时优先读汇总文件；若汇总文件不存在则尝试从旧文件迁移或从单站点文件重建。
#
# 并发模型: 单把可重入锁 LOCK 管全部共享状态（与单文件版一致，
# 避免多锁交叉死锁）；浏览器会话状态（捕获/打开网站）在 capture.py 中，
# 但同样复用本模块的 LOCK。
# ============================================================

import json
import os
import threading
import uuid
import webbrowser
from datetime import datetime

import config

# ==================== 共享状态 ====================
LOCK = threading.RLock()     # 全局可重入锁

_credentials = {}    # {site: {username, password, cookies, token, headers, login_url, notes, storage_state, user_agent, ...}}
_pending = {}        # {request_id: {site, login_url, reason, status, created_at, auto}}
_waiters = {}        # {site: [threading.Event]} 长轮询等待队列


def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ==================== 单站点文件读写 ====================

def _save_site_file(site):
    """保存单个站点的凭据到独立文件（data/{site}.json）

    调用方须已持有 LOCK（RLock 可重入）。
    """
    if site not in _credentials:
        return
    fpath = config.site_file_path(site)
    try:
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(_credentials[site], f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('[错误] 保存站点文件失败 %s: %s' % (site, e))


def _delete_site_file(site):
    """删除单个站点的独立文件（调用方须已持有 LOCK）"""
    fpath = config.site_file_path(site)
    try:
        if os.path.exists(fpath):
            os.remove(fpath)
    except Exception as e:
        print('[错误] 删除站点文件失败 %s: %s' % (site, e))


def _save_all_site_files():
    """将内存中所有站点凭据逐一写入独立文件（迁移/重建场景用）"""
    with LOCK:
        for site in list(_credentials.keys()):
            _save_site_file(site)


def save_site_file(site):
    """保存单个站点的凭据到独立文件（公开接口，自动加锁）"""
    with LOCK:
        _save_site_file(site)


def _load_from_site_files():
    """从 data 目录下的单站点文件重建凭据字典

    扫描 data/ 下所有不以 _ 开头的 .json 文件，文件名（去扩展名）即站点标识。
    """
    creds = {}
    if not os.path.isdir(config.DATA_DIR):
        return creds
    for fname in os.listdir(config.DATA_DIR):
        if fname.startswith('_') or not fname.endswith('.json'):
            continue
        fpath = os.path.join(config.DATA_DIR, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                cred = json.load(f)
            site = fname[:-5]  # 去掉 .json
            if isinstance(cred, dict):
                creds[site] = cred
        except Exception:
            pass
    return creds


# ==================== 持久化 ====================

def load_data():
    """从汇总文件加载凭据和待处理请求。

    优先级：_credentials.json > 旧 credentials.json（迁移）> 单站点文件（重建）。
    """
    global _credentials, _pending

    # 1. 优先从汇总文件加载
    if os.path.exists(config.DATA_FILE):
        try:
            with open(config.DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _credentials = data.get('credentials', {})
                _pending = data.get('pending', {})
        except Exception as e:
            print('[警告] 加载汇总文件失败: %s' % e)
            _credentials = {}
            _pending = {}
        return

    # 2. 旧文件迁移（credentials.json → _credentials.json + 单站点文件）
    if os.path.exists(config.OLD_DATA_FILE):
        try:
            with open(config.OLD_DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _credentials = data.get('credentials', {})
                _pending = data.get('pending', {})
            save_data()
            _save_all_site_files()
            print('[迁移] 已从旧文件 credentials.json 迁移到 _credentials.json + %d 个单站点文件'
                  % len(_credentials))
        except Exception as e:
            print('[警告] 旧文件迁移失败: %s' % e)
            _credentials = {}
            _pending = {}
        return

    # 3. 从单站点文件重建（汇总文件丢失但单站点文件存在）
    rebuilt = _load_from_site_files()
    if rebuilt:
        _credentials = rebuilt
        _pending = {}
        save_data()
        print('[恢复] 从单站点文件重建了 %d 个站点凭据到汇总文件' % len(rebuilt))
    else:
        _credentials = {}
        _pending = {}


def save_data():
    """保存数据到 JSON 文件（清理已完成请求，防止文件膨胀）"""
    with LOCK:
        completed = sorted(
            [(rid, r) for rid, r in _pending.items() if r.get('status') != 'pending'],
            key=lambda x: x[1].get('completed_at', x[1].get('created_at', '')),
            reverse=True
        )
        for rid, _ in completed[config.MAX_COMPLETED_KEEP:]:
            _pending.pop(rid, None)
        data = {'credentials': _credentials, 'pending': _pending}
        os.makedirs(config.DATA_DIR, exist_ok=True)
        try:
            with open(config.DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print('[错误] 保存数据失败: %s' % e)


# ==================== 长轮询 ====================

def notify_waiters(site):
    """通知所有等待该站点凭据的线程"""
    with LOCK:
        if site in _waiters:
            for event in _waiters[site]:
                event.set()
            _waiters[site] = []


def register_waiter(site):
    """注册一个长轮询等待者，返回其事件对象"""
    event = threading.Event()
    with LOCK:
        _waiters.setdefault(site, []).append(event)
    return event


def unregister_waiter(site, event):
    with LOCK:
        if site in _waiters and event in _waiters[site]:
            _waiters[site].remove(event)


def list_sites():
    """列出所有已保存凭据的站点（排序）"""
    with LOCK:
        return sorted(_credentials.keys())


def has_credentials(site):
    with LOCK:
        return site in _credentials


def get_credential_copy(site):
    """取得凭据快照；不存在返回 None"""
    with LOCK:
        if site in _credentials:
            return dict(_credentials[site])
        return None


def mark_used(site):
    """记录一次凭据使用（使用次数 + 最后使用时间）"""
    with LOCK:
        if site in _credentials:
            _credentials[site]['use_count'] = _credentials[site].get('use_count', 0) + 1
            _credentials[site]['last_used'] = now_str()


# ==================== 登录请求 ====================

def create_login_request(site, login_url='', reason='机器人需要人工登录', auto=False):
    """创建登录请求，返回 request_id；必要时弹出管理页提醒人工处理"""
    request_id = uuid.uuid4().hex[:8]
    with LOCK:
        _pending[request_id] = {
            'site': site,
            'login_url': login_url,
            'reason': reason,
            'status': 'pending',
            'created_at': now_str(),
            'auto': auto
        }
    save_data()

    # 自动打开管理页提醒（后台无人值守场景可通过 GUSSARI_AUTO_OPEN=0 关闭）
    if config.AUTO_OPEN_BROWSER:
        try:
            webbrowser.open(config.web_url('/#pending'))
        except Exception:
            pass

    return request_id


def has_pending_request(site):
    """该站点是否存在待处理的登录请求"""
    with LOCK:
        return any(r['site'] == site and r['status'] == 'pending'
                   for r in _pending.values())


def list_pending():
    """列出待处理登录请求（按创建时间倒序）"""
    with LOCK:
        pending = [dict(r, request_id=rid)
                   for rid, r in _pending.items()
                   if r['status'] == 'pending']
    pending.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return pending


def get_request(request_id):
    """按 ID 取登录请求（不存在返回 None）"""
    with LOCK:
        return dict(_pending[request_id]) if request_id in _pending else None


def delete_credentials(site):
    """删除凭据（汇总文件 + 单站点文件同步删除），返回是否删除成功"""
    with LOCK:
        if site not in _credentials:
            return False
        del _credentials[site]
        _delete_site_file(site)
    save_data()
    return True


def report_result(site, success, message=''):
    """记录凭据使用结果"""
    with LOCK:
        if site not in _credentials:
            return False
        _credentials[site]['last_result'] = 'success' if success else 'fail'
        _credentials[site]['last_result_message'] = message
        _credentials[site]['last_result_time'] = now_str()
    save_data()
    save_site_file(site)
    return True


# ==================== 凭据写入 ====================

def store_credential(site, body, complete_pending=True):
    """保存/合并凭据数据（HTTP 保存、登录请求完成、网页捕获共用）

    complete_pending=True 时，同时把该站点所有待处理登录请求标记为已完成
    （凭据已到手，挂着的请求实际已被满足）。
    """
    now = now_str()
    with LOCK:
        existing = _credentials.get(site, {})
        # URL 自动补全协议，默认 https
        login_url = body.get('login_url') or existing.get('login_url', '')
        if login_url and not login_url.startswith(('http://', 'https://')):
            login_url = 'https://' + login_url
        _credentials[site] = {
            'username': body.get('username', ''),
            'password': body.get('password', ''),
            'cookies': body.get('cookies', ''),
            'token': body.get('token', ''),
            'headers': body.get('headers', {}),
            'login_url': login_url,
            'notes': body.get('notes', ''),
            # 浏览器自动化：完整会话状态（cookies + localStorage + sessionStorage）
            'storage_state': body.get('storage_state') or existing.get('storage_state'),
            'user_agent': body.get('user_agent') or existing.get('user_agent', ''),
            'created_at': existing.get('created_at', now),
            'updated_at': now,
            'use_count': existing.get('use_count', 0),
            'last_used': existing.get('last_used'),
            'last_result': existing.get('last_result'),
            'last_result_message': existing.get('last_result_message'),
            'last_result_time': existing.get('last_result_time')
        }
        if complete_pending:
            for rid, r in _pending.items():
                if r['site'] == site and r['status'] == 'pending':
                    r['status'] = 'completed'
                    r['completed_at'] = now
    save_data()
    save_site_file(site)
    notify_waiters(site)
    return dict(_credentials[site])
