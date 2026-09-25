# gussari — AI 重放师

AI 访问网站时的凭据管理工具：**人工登录一次，机器人自动复用**。

机器人（AI Agent / 爬虫 / 自动化工具）首次访问网站时向 gussari 要凭据；没有凭据就自动发起人工登录请求，人工在浏览器里完成一次登录，之后 AI 就能免登录持续访问（Cookie / Token / 完整浏览器会话三种模式）。

## 工作流程

```
机器人首次访问网站 → 调 API 检查凭据 → 无凭据 → 自动发起登录请求
→ 人工在浏览器中完成一次登录（三种方式任选） → 凭据保存
→ 机器人长轮询拿到凭据 → 使用凭据访问网站 → 后续访问直接复用
```

## 功能

- **凭据管理页**（`http://127.0.0.1:3366/`）：凭据增删改查、使用统计、状态标记
- **浏览器登录捕获（推荐）**：服务端拉起 Chrome/Edge，人工正常登录一次，一键保存完整会话（cookies + localStorage + sessionStorage + User-Agent）
- **AI 两种复用模式**：
  1. HTTP 请求模式：`build_headers()` 拿请求头直接调 API
  2. 浏览器自动化模式：`create_browser_context()` 注入会话状态免登录操作网页（Playwright）
- **登录请求协作**：机器人触发 → 管理页提醒（声音 + 标题闪烁 + 系统通知）→ 人工提交 → 机器人长轮询自动获取
- **会话失效闭环**：AI 检测到失效可 `report_expired()`，自动发起新一轮人工登录
- **一键安装 playwright**：管理页内置可视化进度安装（清华镜像）
- **零依赖**：服务端仅 Python 标准库，前端自包含单页无任何外部资源

## 快速开始

```bash
python server.py
```

启动后访问 **http://127.0.0.1:3366**。

### 后台静默运行

- **双击 `start.bat`**：经 `start-hidden.vbs` 隐藏窗口后台启动，无控制台窗口残留，日志追加写入 `server.log`；可选参数指定端口（`start.bat 3400`），或直接用 `PORT` 环境变量
- **双击 `stop.bat`**：从 `server.log` 解析实际端口，按端口精确终止服务进程
- **`manifest.json`** 已含 `url` + `startScript` 字段，供 dfd 门户等调度器自动拉起并跳转

### 常用参数

```bash
python server.py --port 3400       # 指定端口（默认 3366）
python server.py --host 0.0.0.0    # 监听所有网卡（局域网可访问）
python server.py --token SECRET    # 启用 Bearer Token 鉴权
python server.py --force           # 端口被占用时先终止旧实例再启动
python server.py --check           # 仅检测端口状态，不启动
```

### AI 客户端接入

```python
from ai_client import CredentialClient, quick_start

# 方式一：拿请求头直接访问
headers = quick_start('example.com')
resp = requests.get('https://example.com/api/data', headers=headers)

# 方式二：浏览器自动化免登录
from playwright.sync_api import sync_playwright
client = CredentialClient()
with sync_playwright() as p:
    browser, context = client.create_browser_context('example.com', p)
    if context:  # 无已保存会话时为 None，需先人工登录捕获
        page = context.new_page()
```

命令行工具（`client/` 目录）：

```bash
python client/ai_client_example.py --quick example.com https://example.com/login
python client/ai_client_example.py --browser example.com https://example.com/home
python client/browser_login.py example.com https://example.com/login   # 人工登录捕获
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sites` | 列出所有已保存凭据的站点 |
| GET | `/api/credentials/{site}` | 获取凭据（`?wait=true` 长轮询等待，最多 120 秒） |
| POST | `/api/credentials/{site}` | 保存/更新凭据 |
| DELETE | `/api/credentials/{site}` | 删除凭据 |
| POST | `/api/request-login/{site}` | 发起人工登录请求（自动弹管理页提醒） |
| GET | `/api/pending` | 列出待处理的登录请求 |
| POST | `/api/complete-login/{request_id}` | 完成登录请求并保存凭据 |
| POST | `/api/report/{site}` | 报告凭据使用结果（成功/失败） |
| GET | `/api/status/{site}` | 查询凭据状态 |
| POST | `/api/capture-login/{site}` | 网页一键发起浏览器登录捕获 |
| GET | `/api/capture-login/{site}` | 查询捕获会话状态（含当前页面 URL） |
| POST | `/api/capture-login/{site}/complete` | 登录完成后确认保存捕获的会话 |
| POST | `/api/capture-login/{site}/cancel` | 取消捕获并关闭浏览器 |
| POST | `/api/open-site/{site}` | 用已保存的凭据直接打开网站（弹浏览器注入登录态） |
| GET | `/api/dependency` | playwright 依赖探测与一键安装状态 |
| GET | `/api/docs` | 获取本 API 文档（JSON） |

启用 `--token` 后，所有 `/api/*` 请求需携带 `Authorization: Bearer <token>` 或 `?token=<token>`。

## 数据位置

凭据采用双文件存储，默认存于项目根 `data/` 目录：

- **汇总文件** `data/_credentials.json`：所有站点凭据集中保存（含待处理登录请求）
- **单站点文件** `data/{site}.json`：每个站点一个独立文件，内容与汇总文件中该站点的凭据一致

保存/更新凭据时同步写入两侧；删除凭据时同步删除两侧。启动时优先读汇总文件，
若不存在则自动从旧 `credentials.json` 迁移或从单站点文件重建。

可用环境变量 `GUSSARI_DATA_DIR` 重定向数据目录。

## 安全说明

- 服务默认仅允许本机（127.0.0.1）访问，未开放 CORS（防止任意网页偷取凭据）
- 可通过 `--token` 参数或环境变量 `CRED_SAVER_TOKEN` 指定访问令牌
- 未指定 token 时无鉴权（本地使用可接受，勿暴露到网络）
- `data/_credentials.json` 和 `data/*.json` 已列入 `.gitignore`，凭据不会进 git 仓库

## 测试

```bash
python test/test_api.py
```

API 集成测试：临时数据目录隔离（不触碰真实凭据），子进程随机端口启动服务，urllib 全端点验证，含鉴权模式。

## 项目结构

```
gussari/
├── server.py                     # 服务入口 (参数/端口检测/启动)
├── src/
│   ├── config.py                 # 配置层 (路径/端口/令牌/常量)
│   ├── store.py                  # 数据层 (凭据存储/登录请求/长轮询等待)
│   ├── capture.py                # 浏览器自动化层 (登录捕获/打开网站/依赖安装)
│   ├── api.py                    # HTTP API 层 (路由/鉴权/端点)
│   └── docs.py                   # API 文档数据层
├── web/
│   └── index.html                # 管理单页 (自包含, 零外部资源)
├── client/
│   ├── ai_client.py              # AI 客户端 SDK (CredentialClient)
│   ├── ai_client_example.py      # 使用示例 (demo/--quick/--browser)
│   └── browser_login.py         # CLI 人工登录捕获器
├── data/
│   ├── _credentials.json          # 汇总凭据文件 (gitignore, 敏感)
│   └── {site}.json                # 单站点凭据文件 (每站一个, gitignore)
├── test/
│   └── test_api.py               # API 集成测试
├── docs/
│   └── design.md                 # 层次化设计说明
├── server.py 之上的启动与集成文件:
│   ├── start.bat                 # 后台静默启动 (双击, 可选端口参数)
│   ├── start-hidden.vbs          # 隐藏窗口启动器 (start.bat 内部调用)
│   ├── stop.bat                  # 按端口精确停止服务
│   ├── manifest.json             # 项目清单 (url + startScript, 供 dfd 门户集成)
│   └── requirements.txt
└── LICENSE                       # AGPL-3.0
```

## 加入 dfd 门户集群

项目已内置 `manifest.json`（含 `url` + `startScript`）。在 dfd 门户的 `services/` 下放置
指向本项目的转发目录（`manifest.json` + `start.bat`，详见 `docs/design.md`），
即可在门户上探测 gussari 在线状态、一键后台启动、直达管理页。
