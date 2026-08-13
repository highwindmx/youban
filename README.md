# 友伴

基于 **FastAPI + DeepSeek** 的本地 AI 助手，支持流式对话、Skill 工具调用、**长期记忆**、Markdown 渲染、对话持久化与历史管理。

## 功能

-   流式对话（SSE 打字机效果）
-   **Markdown 渲染**：标题/列表/代码块/表格/引用/链接等，代码块带「复制」按钮，离线可用、不依赖 CDN
-   **工具调用卡片**：每次工具调用渲染为可折叠卡片（图标 + 工具名 + 运行中/成功/失败 状态徽标），参数与结果可展开、可复制
-   多轮 **agent loop**：模型可自行决定调用工具，循环执行直到任务完成（带迭代上限）
-   内置 13 个 Skill 工具：
    -   `read_file` / `write_file` —— 读写工作区内文件
    -   `list_dir` —— 列出目录
    -   `search_text` —— 工作区内子串搜索
    -   `run_command` —— 执行受控 shell 命令（**命令沙箱**：拦截 powershell/cmd/bash/curl 等危险基名，禁用 `; && || &` 链式操作符）
    -   `run_code` —— 运行 Python / JS 代码（整组子进程清理 + 64KB 输出截断）
    -   `web_search` —— 联网检索公开信息（DuckDuckGo，无需 key，失败有兜底提示）
    -   `manage_dir` —— 对 `TARGET_DIR` 做受限文件操作（增删查/移动/建目录）
    -   `parse_document` —— **办公文档解析**：把 pdf/docx/xlsx/pptx/csv/html/eml/msg 及纯文本解析为 Markdown/文本（带长度上限与截断提示），供模型直接阅读；RAG 索引也会自动纳入这些格式
    -   `rag_index` / `rag_query` —— 对 `DOC_ROOT` 本地文档（含 Office/PDF）建索引并检索（轻量 RAG）
    -   `remember` / `forget` —— **长期记忆**：跨会话保存/删除用户偏好与关键事实，自动注入 system prompt
-   **上下文与记忆优化**：工具调用/结果落库并回喂模型，按 token 预算滑动截断，避免多轮「失忆」与上下文爆窗
-   **跨会话搜索** `/api/search` 与 **会话导出** `/api/export`（Markdown，含工具调用记录）
-   **token 成本统计**：累计 token 实时展示并落库
-   **多模态**：可附图片发送（base64），配置 `DEEPSEEK_VISION_MODEL` 后切换视觉模型理解图片
-   左侧栏历史任务管理：会话列表、切换、新建、删除、重命名
-   对话中途可终止（按会话维护停止事件）
-   文件拖拽上传 + 桌面版原生「选择文件/目录」对话框

### 桌面版（友伴 Desktop）

额外提供 **pywebview 桌面版**：双击即用，后端逻辑与网页版完全一致，只是把本地服务塞进一个原生窗口，并获得浏览器做不到的**系统原生文件/目录选择能力**。

-   启动脚本：`desktop.bat`（Windows）/ `desktop.ps1` / `desktop.sh`
    
    ```bash
    uv run python desktop_app.py    # Windows 也可直接双击 desktop.bat
    ```
    
-   桌面版自动设置 `MB_DESKTOP=1`，从而放开文件沙箱；网页版默认沙箱开启。
-   前端检测到 `window.pywebview.api` 时会显示「选择文件 / 选择目录」按钮，纯网页版仍可用拖拽上传。

> ⚠️ 拖拽路径说明：浏览器沙箱只能拿到文件名；本项目已改为**拖入即上传到工作区**，后端返回文件真实可达的绝对路径，网页版/桌面版都能用。桌面版另提供原生对话框，可直接拿到本机任意真实路径。

## 快速开始

1.  安装依赖（用 uv）
    
    ```bash
    uv sync
    ```
    
    > 若官方 PyPI 超时，可使用镜像：`uv sync --index-url https://pypi.tuna.tsinghua.edu.cn/simple`
    
2.  配置 API Key
    
    ```bash
    cp .env.example .env
    # 编辑 .env，填入 DEEPSEEK_API_KEY=sk-xxx
    ```
    
3.  启动（任选其一）
    
    ```bash
    # 网页版
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
    
    # 桌面版
    uv run python desktop_app.py    # Windows 也可直接双击 desktop.bat
    ```
    
4.  网页版打开 [http://127.0.0.1:8000；桌面版自动弹出原生窗口。](http://127.0.0.1:8000%EF%BC%9B%E6%A1%8C%E9%9D%A2%E7%89%88%E8%87%AA%E5%8A%A8%E5%BC%B9%E5%87%BA%E5%8E%9F%E7%94%9F%E7%AA%97%E5%8F%A3%E3%80%82)
    

## 环境变量（.env）

变量

说明

默认

DEEPSEEK\_API\_KEY

DeepSeek API Key（必填）

空

DEEPSEEK\_BASE\_URL

API 地址

[https://api.deepseek.com](https://api.deepseek.com)

DEEPSEEK\_MODEL

模型名

deepseek-chat

DEEPSEEK\_VISION\_MODEL

视觉模型名（可选，配置后才支持图片理解）

空

HOST / PORT

监听地址

127.0.0.1:8000

WORKSPACE\_ROOT

通用文件操作沙箱根目录

项目根目录

TARGET\_DIR

目录操作工具可管理的特定目录

项目根/target\_dir

DOC\_ROOT

RAG 文档检索目录

项目根/docs

MAX\_AGENT\_ITERATIONS

单次对话最大工具轮数

8

DB\_PATH

SQLite 路径

mini\_wb.db

MB\_DESKTOP

设为 1 即桌面模式（自动放开文件沙箱）

未设置

MB\_SANDBOX

文件沙箱开关；`true` 限制在工作区，`false` 允许任意本地路径

网页版 true / 桌面版 false

> 运行环境（Windows/macOS/Linux）由 `os.name` 自动识别，无需手动配置；如需强制指定可在 `.env` 设置 `OS=Windows`。

## 主要 API

方法

路径

说明

GET

`/api/conversations`

会话列表（含标题预览、时间、累计 token，倒序）

POST

`/api/conversations`

新建会话，返回 `{id, title}`，可选 `?conversation_id=` 指定 ID

GET

`/api/history/{id}`

某会话的消息历史（含工具调用记录）

POST

`/api/chat`

发送消息（SSE 流式），首次自动以首条消息作标题

POST

`/api/chat/stop?conversation_id=`

中止指定会话的生成

POST

`/api/upload`

上传文件到工作区，返回真实绝对路径（multipart `files`）

POST

`/api/search?q=`

跨会话全文搜索历史消息

GET

`/api/export/{id}`

导出会话为 Markdown（含工具调用记录）

POST

`/api/conversations/{id}/rename`

重命名会话 `{ "title": "..." }`

DELETE

`/api/conversations/{id}`

删除会话（级联删消息，幂等返回 200）

## 项目结构

```
app/
  __init__.py  包入口
  config.py    配置、OS 识别、沙箱根目录、MB_SANDBOX 开关
  db.py        SQLite 持久化（会话、消息、工具调用、长期记忆、token 累计）
  skills.py    Skill 工具注册表与执行（含沙箱校验、命令护栏、RAG、长期记忆）
  llm.py       DeepSeek 接入 + agent loop（含记忆注入、上下文预算、重试）
  main.py      FastAPI 路由
  schemas.py   请求模型
static/
  index.html   前端（左侧栏、Markdown 渲染、工具卡片、搜索、导出、上传）
  icon.png     应用图标（512×512）
  icon_256.png 应用图标（256×256）
  icon.svg     应用图标（矢量）
desktop_app.py     桌面版启动器（pywebview）
desktop.bat / desktop.ps1 / desktop.sh    桌面版快捷启动
```

## 安全提示

-   文件操作被限制在沙箱内（WORKSPACE\_ROOT / TARGET\_DIR / DOC\_ROOT），越界路径会被拒绝。
-   `run_command` 命令沙箱：危险命令基名黑名单（powershell / cmd / bash / curl / certutil 等）+ 禁用 `; && || &` 链式操作符，防提示注入执行任意命令。
-   `run_code` / `run_command`：整组子进程清理（防孤儿进程），stdout 截断 64KB，防死循环灌爆上下文/内存。
-   桌面版读任意绝对路径时记录审计日志到 `mini_wb.audit.log`。
-   `run_command` / `run_code` 直接执行代码，仅用于本地可信环境，请勿暴露到公网。
-   高危通配/越级删除命令会被拦截，删除请走 `manage_dir(action='delete')`（限定 TARGET\_DIR 内）。

  

Powered by WorkBuddy