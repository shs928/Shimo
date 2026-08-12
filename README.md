# 拾墨 · Shimo

拾墨（Shimo）——轻量、文件优先的个人知识库。Markdown 文件就是你的知识资产，可在线编辑、支持层级目录、回收站与全文搜索。本地一键启动，后续可部署云端。

## 快速开始

要求：Python 3.10+（Windows / macOS / Linux）。

### Windows

双击 `scripts/start.bat`，或命令行运行：

```bat
scripts\start.bat
```

首次运行会自动创建虚拟环境、安装依赖，然后打开 http://127.0.0.1:8848。

### macOS / Linux

```bash
bash scripts/start.sh
```

### 前端构建（仅开发时需要）

发布压缩包已包含 `frontend/dist`，普通用户无需 Node。修改前端后需在 `frontend/` 下执行：

```bash
npm install
npm run build
```

## 使用

1. 首次打开会进入初始化页，设置访问密码。
2. 左侧文件树：新建笔记（＋）、新建文件夹（📁＋），点击文件打开编辑。
3. 编辑器：编辑 / 阅读 / 分屏三种模式，自动保存（停止输入 1 秒后）或手动保存。
4. 保存冲突：文件若被其他工具/设备修改，保存会返回 412 冲突，提示先刷新。
5. 删除的文件进入回收站（`.trash/`），可恢复或清空。

## 数据在哪里

```
vault/   你的知识文件（Markdown、附件），拷贝即备份
data/    应用数据（会话、配置），可整体删除不影响 vault 内容
```

## 开发

```bash
# 后端测试
python -m pytest tests/ -q

# 启动后端（开发，已构建前端）
python -m app
```

## 配置

环境变量（可选）：

| 变量 | 说明 |
|---|---|
| `SHIMO_HOST` / `SHIMO_PORT` | 监听地址，默认 `127.0.0.1:8848` |
| `SHIMO_VAULT_PATH` / `SHIMO_DATA_PATH` | Vault / 数据目录路径 |
| `SHIMO_INITIAL_PASSWORD` | 首次初始化密码（云端部署用） |

## 当前进度

- [x] 真实文件树（懒加载、展开/折叠）
- [x] CodeMirror 在线编辑 + Markdown 预览（编辑/阅读/分屏）
- [x] 自动保存、手动保存、保存状态展示
- [x] ETag 乐观锁（多端修改冲突检测）
- [x] 新建 / 移动（预检）/ 删除（回收站）/ 恢复 / 清空
- [x] 单用户密码认证（Argon2id + HttpOnly Cookie + CSRF 防护）
- [x] 路径安全（穿越/符号链接/非法文件名拦截）
- [x] BOM / CRLF 保留的无损写入
- [x] AI 问答（RAG：FTS/向量检索 + SSE 流式 + 来源引用）
- [x] 多 Provider 管理（OpenAI 兼容服务，含环境变量注入）
- [x] 自由对话（多轮上下文）+ 选中文本 AI 操作（右键菜单）
- [x] 语义搜索端点 + 后台增量向量索引
- [x] AI Agent（function calling：检索/读写笔记/SQL/图片，写操作需确认）
- [x] MCP 外部服务器工具接入

## 路线图

- M2：Obsidian WikiLink、YAML front matter、KaTeX、Mermaid、附件相对路径、大纲
- M3：全文搜索（FTS5）、反链/提及、图谱、文件监听与索引重建
- M4：版本历史、工作区恢复、移动端完整适配
- M5：导入导出、备份恢复、便携版 exe、Docker 部署
- M6：可插拔 AI 问答（RAG）
