# gussari 层次化设计说明

> 记录从单文件 `credential_saver.py`（1687 行）到层次化项目的拆分设计，
> 方便后续维护者理解各层职责与依赖方向。

## 1. 拆分背景

原项目（E:\aicredit）是三个平铺的 Python 文件：

| 原文件 | 行数 | 内容 |
|--------|------|------|
| credential_saver.py | 1687 | 服务端全部逻辑：配置、数据持久化、HTML 页面字符串、HTTP handler、浏览器捕获线程、pip 安装线程、main |
| ai_client_example.py | 406 | AI 客户端 SDK 类 + demo（库与示例混在一起） |
| browser_login.py | 217 | CLI 人工登录捕获器 |

问题：单文件巨石（HTML 内嵌 630 行字符串）、库与示例耦合、数据文件与代码混放、
启动方式依赖命令行窗口（`启动凭据保存器.bat` 前台阻塞运行）。

## 2. 层次划分

```
server.py          入口层：argparse 参数、端口检测（--check/--force）、启动横幅、main
src/config.py      配置层：全部路径/端口/常量/令牌；运行期状态（PORT/ACCESS_TOKEN）由此写入
src/store.py       数据层：凭据与登录请求的内存状态、双文件持久化（汇总 _credentials.json + 单站点 {site}.json）、长轮询等待队列
src/capture.py     浏览器自动化层：登录捕获会话、用凭据打开网站、playwright 探测与一键安装
src/api.py         HTTP API 层：路由分发、鉴权、请求/响应编解码；业务委托 store/capture
src/docs.py        文档层：/api/docs 的接口清单与示例（纯函数）
web/index.html     前端：管理单页（原 HTML_PAGE 字符串原样提取）
client/            AI 客户端 SDK（ai_client.py）+ 示例 + CLI 捕获器
data/              凭据数据（双文件存储：汇总 _credentials.json + 单站点 {site}.json；GUSSARI_DATA_DIR 环境变量可重定向，测试隔离用）
```

## 3. 依赖方向（单向，无循环）

```
server.py ──► api.py ──► store.py ──► config.py
    │            │          │
    │            ├──► capture.py ──► store.py, config.py
    │            ├──► docs.py （纯函数，无依赖）
    │            └──► config.py
    └──► store.py / capture.py / config.py（启动前加载与探测）
```

关键约定：

- **单锁原则**：全项目共用 `store.LOCK` 一把可重入锁（与单文件版一致），避免多锁交叉死锁；capture.py 的会话状态（`_captures/_opens/_install_state`）也复用该锁。
- **Playwright 线程模型**：sync API 不允许跨线程调用，每个捕获/打开会话由专用 daemon 线程运行，主线程经 session dict + 锁轮询通信（与原实现一致）。
- **config 运行期状态**：`config.PORT / config.ACCESS_TOKEN` 由 server.py 启动时写入，api/store 通过模块属性引用（`import config`），保证长轮询 wait_url、弹窗地址等始终取运行期真实端口。
- **模块私有下划线状态**不再跨层访问：store 暴露 `list_sites / get_credential_copy / mark_used / has_pending_request / get_request / report_result` 等读写接口。

## 4. 行为变更点（相对单文件版）

| 变更 | 原因 |
|------|------|
| 启动横幅不再自动 `webbrowser.open` | 后台服务模式应保持安静；入口交给 dfd 门户/管理页。登录请求到来时的弹窗提醒保留（可用 `GUSSARI_AUTO_OPEN=0` 关闭） |
| 端口参数改为 `--port` / `PORT` 环境变量 | 对齐 MetalSkeleton 后台调度约定，vbs 经 PORT 传端口 |
| 数据文件改为双文件存储 | 单文件 `credentials.json` 拆为汇总 `_credentials.json` + 单站点 `{site}.json`；保存/删除时同步写入两侧；启动时优先读汇总文件，旧文件自动迁移，汇总丢失时可从单站点文件重建 |
| `--token` 从 sys.argv hack 改为 argparse 参数 | 参数解析正规化；`CRED_SAVER_TOKEN` 环境变量兼容保留 |
| 数据文件移至 `data/credentials.json` | 代码与数据分离；`.gitignore` 屏蔽防泄漏 |
| HTML 从字符串提取为 `web/index.html` | 可独立编辑、浏览器直接打开预览、去除转义噪音 |
| 新增 `--check / --force` | 对齐 MetalSkeleton 运维习惯 |

## 5. 后台启动链路（仿 MetalSkeleton）

```
双击 start.bat [端口]
   └─ python --version 探测（避开 WindowsApps 商店 stub）
      └─ wscript start-hidden.vbs [端口]
         ├─ sh.CurrentDirectory = 项目根
         ├─ 设置 PROCESS 级 PORT 环境变量
         ├─ server.log 追加启动记录
         └─ sh.Run "cmd /c python -u server.py >> server.log 2>&1", 0(False)
              └─ 无窗口后台运行；-u 保证横幅即时落盘
双击 stop.bat
   └─ PowerShell 从 server.log 正则解析 127.0.0.1:<port>（取最后一条）
      └─ Get-NetTCPConnection 按端口找 LISTENING 进程 → Stop-Process
```

- 启动窗口零残留：cmd 窗口由 vbs 以隐藏方式拉起，start.bat 自身立即结束。
- `manifest.json`（icon/name/description/tags/url/startScript）对齐 dfd 门户 `services/`
  扫描协议；dfd 通过 HTTP 探测 `url` 判断在线状态。

## 6. 双文件存储策略

凭据持久化采用双文件策略，保证数据冗余与可恢复性：

```
data/
├── _credentials.json        # 汇总文件：所有站点凭据 + 待处理请求
├── www.caishi.cn.json        # 单站点文件：仅含该站点的凭据数据
└── portal.company.com.json   # 单站点文件：另一个站点
```

**写入**：保存/更新凭据时，先更新内存，再同步写入汇总文件和单站点文件。
**删除**：删除凭据时，同时删除单站点文件并更新汇总文件。
**加载**：启动时按优先级加载：
1. `_credentials.json`（汇总文件）— 正常启动
2. `credentials.json`（旧文件）— 自动迁移到双文件格式
3. 单站点文件 `*.json`（汇总文件丢失时）— 从单站点文件重建汇总

两文件中同一站点的凭据数据保持一致。汇总文件还额外包含待处理登录请求（`pending`）。

## 7. 接入 dfd 门户（services/ 转发器）

dfd 门户扫描 `services/<名称>/` 子目录，读取其中 `manifest.json` 并在门户上
展示状态探测与启动按钮。gussari 主项目位于 `E:\workspace\numeron\gussari`，
为避免整个项目被复制/junction 进 dfd 仓库，采用**轻量转发器**：

```
dfd/services/gussari/
├── manifest.json   # 与主项目一致（url 指向 http://127.0.0.1:3366/）
└── start.bat       # call "<主项目绝对路径>\start.bat" %*  —— 转发到真实启动器
```

- 门户状态探测走 HTTP（`url`），与文件位置无关，转发器即可满足。
- 启动按钮在转发器目录运行 `start.bat`，`call` 到主项目后 `%~dp0` 定位主项目，
  后续链路（vbs → server.py）全部在主项目内解析，端口/日志/数据均落在主项目。
