# 拾墨前端重构与项目收尾实施计划

本计划按用户要求分为：**前端视觉重构 → P0 安全/一致性 → P1 AI 稳定性 → P2 产品路线图补齐 → 全量验收**。各阶段连续实施，每一阶段必须通过对应测试和构建后才进入下一阶段。

> 安全前置提醒：`data/ai.json` 中现有非空 API Key 已进入本地工作区和审计上下文，应由用户在供应商侧立即轮换。代码实施会把凭据迁移到系统凭据库并从 JSON 删除，但无法代替供应商侧撤销旧 Key。

---

## 阶段 1：按 frontend-design 技能重构视觉与前端交互：https://github.com/anthropics/skills/tree/main/skills/frontend-design

### 1.1 固定视觉方案：“冷墨书案”

遵循指定 `frontend-design/SKILL.md`：先形成明确设计主张，避免通用 Dashboard、紫色 AI 渐变、玻璃卡片、到处相同圆角和无意义动画。

视觉语言：
- **案面** `#E9EEF0`：应用外壳
- **纸面** `#FBFCFA`：文稿、输入区
- **主墨** `#17262D`：正文、蓝黑“墨脊”导航
- **淡墨** `#68777D`：路径、说明
- **青墨** `#266F78`：链接、焦点、当前项、引用
- **朱批** `#B64A3C`：冲突、错误、危险操作、Agent 写确认
- 深色主题使用对应语义 token，不硬编码组件色

字体角色（无 CDN、无新增运行时字体依赖）：
- 品牌/文档标题：系统楷体回退栈
- Markdown 阅读：系统宋体/衬线回退栈
- UI：系统现代无衬线
- 代码/路径：Cascadia Code / JetBrains Mono / Consolas 回退

唯一记忆点：左侧蓝黑色垂直“墨脊”，同时承担品牌和导航；除此之外不增加第二套装饰语言。

### 1.2 重构样式架构

把单一 `frontend/src/style.css` 拆成：
- `styles/tokens.css`：颜色、字体、字号、间距、控件高度、层级、动效
- `styles/base.css`：reset、表单、按钮、focus-visible、无障碍
- `styles/workbench.css`：墨脊、顶部、三栏、标签、抽屉
- `styles/document.css`：CodeMirror、Markdown 阅读、KaTeX、Mermaid
- `styles/assistant.css`：AI、Agent、来源、工具日志、确认
- `styles/responsive.css`：1024/768 断点、100dvh、安全区、移动端 sheet
- `style.css` 仅作为导入入口

同时：
- `main.ts` 导入 `katex/dist/katex.min.css`
- 所有交互元素统一 `:focus-visible`
- 增加 `prefers-reduced-motion` 降级
- 用明确 class 替换 `.context-tabs button` 等高耦合选择器
- Emoji/Unicode 工具图标统一替换为现有 Lucide 图标

### 1.3 重构工作台信息架构

修改 `App.vue`，必要时新增：
- `AppHeader.vue`
- `MobileWorkbenchBar.vue`
- `PanelHeader.vue`
- `IconButton.vue`
- `EmptyState.vue`

信息结构：
- 左侧：墨脊 + “目录索引”（文件/搜索/回收站合并为单一 mode）
- 中央：文稿头（标题、可点击路径、保存状态）+ 编辑/阅读/分屏
- 右侧分为两个领域：
    - 边注：属性、反链、出链、图谱
    - 助手：RAG、自由对话、Agent
- AI 配置从消息区拆出为独立 `AiSettings.vue`
- AI/Agent 不再完全依赖当前已打开 Markdown

移动端：
- 新增真实可用的左侧“文件/搜索”入口和右侧“边注/助手”入口
- 同时只允许一个 sheet 打开
- 关闭后焦点返回触发按钮
- 触控目标至少 44px
- 图谱在移动端全屏显示
- 标签页在移动端改为当前文稿 + “已打开笔记”列表

### 1.4 文稿、文件树、搜索和 AI 视觉重构

`EditorView.vue`：
- CodeMirror 使用同一纸面 token、淡墨 gutter、青墨选区
- 阅读宽度 68–72ch，编辑宽度 82–88ch
- 中央宽度不足时禁用分屏
- `isMobile` 改为响应 resize/matchMedia
- reduced-motion 时大纲跳转不用 smooth
- 拖拽上传提供明确投放反馈

`FileTree.vue`：
- 当前目录面包屑与返回根目录
- 明确“新建/上传目标目录”
- 当前文件整行高亮
- 展开状态统一到 store
- treeitem/键盘操作语义
- hover 与 focus-within 都显示操作按钮
- 拖拽时显示“导入到：目录”

`SearchPanel.vue`：
- 移除未经消毒的 `v-html` 高亮，改为安全文本分片
- 结果项改为可聚焦交互元素
- 保留上下键/Enter

`AiPanel.vue` / `AgentChat.vue`：
- 修复 `cfg=null` 首次打开崩溃风险
- 修复匿名事件监听无法移除
- AI 从“聊天气泡”改成研究边注；来源显示标题/章节/行号
- Agent 工具调用改成“执行日志”，状态本地化
- 修正会话历史/新会话图标语义
- 写操作确认使用朱批色并说明目标与影响
- `AiActionMenu` 统一 Lucide、键盘导航、Escape、视口碰撞

`md.ts`：
- Mermaid 根据 light/dark 主题初始化并在主题变化后重渲染
- 保留 DOMPurify 安全边界

### 1.5 阶段 1 验证

- `vue-tsc --noEmit`
- `vite build`
- 用 browser-use/web-gui-tester 做桌面与移动端黑盒检查：
    - 1440×900、1024×768、390×844、360×800
    - 浅色/深色/reduced-motion
    - 登录、文件树、搜索、编辑/阅读/分屏、上传、AI/Agent 入口
- 截图核验：墨脊必须是唯一记忆点，不出现紫色 AI 渐变或通用 Dashboard 卡片

---

## 阶段 2：P0 安全与数据一致性（严格按审计顺序）

### 2.1 API Key 迁移到系统凭据库

新增 `app/rag/secret_store.py`，使用 `keyring`：
- Windows Credential Manager / macOS Keychain / Linux keyring
- 环境变量优先且永不落盘
- keyring 不可用时 fail closed，拒绝保存 Key并提示使用环境变量
- 绝不降级为本地明文

重构 `AiStore`：
- `ai.json` 只保存 Provider 元数据，不保存 `api_key`
- 运行时加载时按 provider_id 从 SecretStore 注入
- 旧 v1/v2 明文 Key：先成功写入系统凭据库，再原子重写 JSON 删除 Key
- Provider 删除时删除 Secret
- 增加 `clear_api_key` 明确语义；字段缺失表示保留，不能再用空字符串混淆
- API 继续只返回 `has_key`

依赖：`keyring>=25,<26`。

测试：
- 保存后原始 JSON 不包含 Key字段或 Key 文本
- 旧配置迁移、环境变量覆盖、清空、Provider 删除
- keyring 不可用时不发生半迁移、不明文降级

### 2.2 AI 全局外呼闸门

- 增加 `active_embedding_config()`、`active_rerank_config()`
- AI 关闭时：
    - 后台 Embedding 不发新批次
    - semantic-search 退化为 FTS-only
    - Agent knowledge_search 不调用 Embedding
    - Rerank 不调用
- 状态拆分：configured / active / worker_alive，不再把调度器存活误称“嵌入中”
- 关闭后允许已发出的同步 HTTP 批次自然结束，但不再发送下一批

测试 monkeypatch 外呼函数，断言 AI 关闭时调用次数为 0。

### 2.3 Agent 工具权限改为 fail-closed

统一授权函数：
- 当前设置中不存在或被禁用：拒绝
- 只读且启用：执行
- 有副作用且启用：必须确认
- 未知工具：拒绝
- 每次执行前重新读取最新配置
- `execute()` 内二次校验，防止其他调用点绕过
- MCP 工具默认禁用或默认全部需确认，只有 allowlist 工具可执行

补充禁用写工具、禁用只读工具、未知工具、配置实时变化、MCP 非 allowlist 测试。

### 2.4 修复 image.analyze 路径越界

- 使用 `normalize_rel()` + `resolve_in_root()`
- 拒绝绝对路径、`..`、隐藏路径、`.trash`、symlink
- 先 stat 检查原始大小，再读取和 base64
- 明确图片扩展/MIME
- 同步修正 Path Guard 中“绝对路径被去前导斜杠”的语义，绝对路径一律拒绝

补路径穿越、Windows 路径、symlink、隐藏目录、超大文件测试。

### 2.5 普通索引重建不得删除 RAG

重构 `Indexer.rebuild()`：
- 不再全表 `DELETE files_meta`
- 清空并重建普通派生表 links/tags/FTS/headings
- 对现有 Markdown upsert 元数据，不删除有效父记录
- 只删除 Vault 已不存在的 stale Markdown 行
- 保留 PDF/Word/TXT/CSV 占位元数据
- 事务失败完整回滚

测试普通 `/index/rebuild` 后 chunks、embedding id/model/vector 保留；stale 文件正确清理。

### 2.6 schema version 正确写回

- `initialize()` 只检测版本，不提前写回
- 新增 `mark_schema_current()`，仅在重建/迁移成功后执行
- version 高于程序版本时拒绝降级
- 损坏版本明确报错
- 依赖 2.5 完成后再实施

新增 `test_db.py` 覆盖首次、旧版、失败、不兼容未来版本。

### 2.7 MCP 按当前 SDK 2.x 重做

依赖固定：`mcp>=2.0,<3`。

整体重写 `McpManager`：
- 专用 asyncio 事件循环线程
- 所有连接/发现/调用/关闭通过 `run_coroutine_threadsafe` 在同一 loop 执行
- 使用 SDK 2.x 高层 `Client`
- 配置明确 transport：`streamable_http` / `sse_legacy`
- 旧配置迁移为 `sse_legacy`，新配置默认 `streamable_http`
- App lifespan 启动 configure、关闭时 close
- 保存 MCP 配置后刷新连接
- Agent 构建 schema 前执行带超时/TTL 的工具发现
- 使用 `tool.name/description/input_schema`
- `call_tool(name,args)` 正确处理 `is_error`、文本、structured content
- 限制 MCP 输出长度，非文本块只返回安全摘要
- URL、重复名称、alias 校验
- MCP 工具默认需确认并受 allowlist 控制

测试使用 SDK 2.x in-process server/真实 Tool 对象，不再用 dict 塞内部字段；禁止真实外网。

### 2.8 PDF/Word 文件树只读预览

新增：
- `app/routers/documents.py`
- `GET /api/v1/documents/preview?path=`
- `frontend/src/components/DocumentPreview.vue`

Tab 改为判别联合：MarkdownTab / DocumentTab。
- `.md` → EditorView
- `.pdf/.docx/.txt/.csv` → 纯文本预览 + 原文件打开/下载
- 文档 tab 无保存/dirty/Markdown 大纲
- 原始文本用 text interpolation，不使用 innerHTML
- PDF 第一版为提取文本 + 新窗口原文件，不放宽全局 X-Frame-Options

补路径安全、超大文档、解析失败、前端 tab 分流测试。

---

## 阶段 3：P1 AI 稳定性

### 3.1 Embedding 模型切换自动重建

- 新增 `ai_meta`（或等价 schema_meta key）保存 embedding signature：provider_id + base_url + model
- signature 变化时事务执行：删除 embeddings、全部 chunks `ai_indexed=0`
- Key 变化不重建，Provider/model/base_url 变化重建
- worker 启动时也比较 signature，覆盖环境变量变化
- 前端提示“模型已变更，后台重新嵌入”

### 3.2 向量批次严格校验

- `len(vectors) == len(chunk_ids)` 才写入
- 校验每个向量非空、维度一致、索引/排序正确
- 成功写入哪些 chunk，只标记哪些为 indexed
- worker 使用配置中的 `embedding.batch`
- 失败状态可重试，不能静默标完成

### 3.3 索引失败可观测

新增轻量 `IndexHealth`/`index_failures` 表：
- path、subsystem、error、attempts、updated_at
- 文件保存成功但索引失败时响应包含 warning，不再假装完全成功
- `/index/stats` 与 AI 状态返回最近失败和重试状态
- 前端显示非阻塞警告与“重试失败项”
- background embedding 暴露 last_error/backoff

### 3.4 自由对话与 Agent 会话可靠持久化

自由对话：
- 首次发送前自动建 session
- 后端即使未传 session_id 也创建并在 SSE 返回 session 事件
- 从内存 dict 改为原子文件或 SQLite 持久化
- 增加会话列表/打开/删除

Agent：
- 前端“新会话”真正调用后端创建
- 持久化完整 Agent history：assistant tool_calls、tool results、确认结果，不只保存可见文本
- 达到 max_iterations 返回明确错误，不发虚假 done
- 会话标题默认取首条用户问题

### 3.5 图片生成 SSRF/MIME/大小保护

新增安全下载器：
- 仅 http/https
- DNS 解析后拒绝 loopback/private/link-local/multicast/reserved/unspecified
- 禁止或逐跳重新校验 redirect
- 流式下载与最大字节数
- Content-Type 必须为 image/*
- Pillow verify 实际图片格式
- b64 同样限制大小、校验图片
- 按真实格式保存扩展名，不统一伪装 PNG

同时将 Vision/ImageGeneration 从 Agent Chat model 拆为独立配置组。

依赖显式声明 `Pillow`。

### 3.6 Provider 协议错误处理

- 检查响应 Content-Type、JSON 结构、error 字段、choices/data 形状
- SSE 错误帧不能被忽略并返回 done
- 非法 JSON/缺字段统一转换为 ProviderError
- listModels/embedding/rerank/image 分别校验契约
- 上游超时、关闭、空响应给结构化错误
- 增加 OpenAI-compatible mock provider 协议测试

---

## 阶段 4：P2 产品路线图补齐

### 4.1 文件 watcher + SSE

新增：
- `watchfiles` 依赖
- `services/watcher.py`：Vault 外部变化监听、500ms 去抖、批量增量索引
- `services/events.py`：每客户端 asyncio queue 的 EventHub
- `GET /api/v1/events` SSE

前端：
- tree_changed 自动刷新树
- 当前文件 clean 时自动重载并提示
- 当前文件 dirty 时进入 conflict，不自动覆盖
- watcher 与应用内写入保持幂等，避免重复风暴

### 4.2 版本历史

新增 `services/history.py` 和 history routes：
- 每次覆盖 Markdown 前保存旧版本快照
- SHA1 去重、默认 50 版/30 天清理
- 列表、读取、diff、恢复、另存为
- Agent update_note 同样进入历史链路
- 前端增加历史面板和差异/恢复确认

### 4.3 工作区恢复

新增 workspace route/store：
- 浏览器生成 device_id
- 保存 tabs、activePath、左右面板 mode、主题、树展开、面板宽度
- 不保存未提交正文
- 登录启动后恢复仍存在的路径，失效路径安全跳过
- 移动端与桌面设备状态隔离

### 4.4 引用随重命名更新

新增 `services/link_refactor.py`：
- move preview 返回受影响引用清单和数量
- 只重写可唯一解析的 Markdown/WikiLink/嵌入
- 跳过代码块和歧义链接
- 先保存历史快照
- 批量临时文件 + rollback 清单，失败不留下半完成状态
- 移动后同步 FTS/RAG/打开标签

### 4.5 ZIP 导入导出

新增 archive service/routes：
- 导入 preview、文件数量/路径/冲突
- skip/rename/overwrite，overwrite 先历史快照
- 防 Zip Slip、条目数/总展开大小/单文件大小限制
- 导出单目录或完整 Vault，排除 `.trash` 和 data/secrets
- 保持真实相对路径和附件关系

### 4.6 备份恢复

- 知识备份：Vault + manifest/hash
- 完整备份：Vault、配置（不含系统凭据）、workspace、history、chat；不含 index.db
- OS 凭据/API Key 不进入归档，恢复后提示重新配置
- restore preview、完整校验、临时目录解压、写锁、原子替换、重建索引
- 自动备份恢复测试核对哈希和随机内容

### 4.7 发行与部署

新增：
- 多阶段 Dockerfile
- docker-compose.yml
- Caddyfile.example（域名通过环境变量；无法替用户配置真实 DNS/证书）
- `/health/ready`：Vault 可写、DB、索引状态
- PyInstaller spec + Windows onedir 构建脚本；macOS/Linux 需各平台构建
- PWA manifest + 简单 service worker：只缓存静态资源，不缓存 API/未保存正文
- 启动脚本改为 requirements hash/version 检测，避免旧 venv 漏装 mcp/pypdf/docx/keyring
- 清理 README 合并冲突标记，并按真实状态更新完成矩阵

### 4.8 前端自动化测试

新增：
- Vitest + Vue Test Utils：store、tab 分流、设置状态、搜索安全高亮、会话创建
- Playwright：桌面/移动真实流程
- 测试专用本地 OpenAI-compatible mock provider 与 MCP in-process server

Playwright 覆盖：
- 初始化/登录
- 新建/保存/冲突/回收站
- 搜索/反链/图谱
- PDF/Word 上传和预览
- 自由对话自动会话
- RAG 来源
- Agent 工具允许/拒绝
- 移动端左/右 sheet
- 浅色/深色/reduced-motion

---

## 阶段 5：全量验收

每阶段持续运行；最终统一执行：
- Python `compileall`
- 全量 pytest（原测试 + 新单元/API/协议测试）
- `vue-tsc --noEmit`
- Vitest
- Vite production build
- Playwright 桌面/移动 E2E
- browser-use GUI 黑盒复核和截图
- 如本机可用：Docker build / PyInstaller Windows onedir 构建冒烟
- 启动脚本在全新临时 venv 中验证依赖可复现

最终交付报告必须：
- 区分已验证、因缺少真实域名/跨平台环境而未验证的项目
- 如任何测试失败，原样报告，不以“基本成功”代替
- README 完成状态必须与实际代码和测试一致
- 不提交或推送代码，除非用户后续明确要求