# Codex 原生交易 Skill 系统（0 付费 API）

本目录是三个交易 skill 的唯一真相源。三个子目录都是可被 Codex 发现和执行的完整 skill：

- `bottom-fishing`：A 股底部区/超跌修复扫描、消息面裁定、影子复验。
- `stock-diagnostic`：单只 A 股持仓深度诊断、6 角色辩论与机械复核。
- `weekly-ashare-rank`：市场闸门、全市场 T+1 排名、验证、复核与复盘。

原 Python 引擎继续负责确定性行情处理、公式、阈值、风控、回测和 HTML renderer；Codex 负责按
`SKILL.md` 的固定 rubric 做网页证据检索、结构化裁定、反方挑战和审计复核。流程不调用付费 LLM API、
付费行情 API、MCP 或外部 agent，只使用原项目免费公开行情源、本地 Python 与 Codex 网页检索。

## 目录结构

```text
claude_code_Trading_skill_no_API_Doc/
├── README.md
├── install_skills.ps1
├── bottom-fishing/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   └── 原引擎、文档、样例与报告
├── stock-diagnostic/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   └── 原引擎、权重、文档与报告
└── weekly-ashare-rank/
    ├── SKILL.md
    ├── agents/openai.yaml
    └── 原引擎、权重、文档、状态与报告
```

非 skill 的共享验收工具位于上一层：
`C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance`。

## 安装到 Codex 工作区

Codex 在工作区的 `.agents/skills/<name>/SKILL.md` 发现 skill。运行：

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\install_skills.ps1"
```

默认将三个源目录以 junction 安装到：
`C:\Trading_analysis\.agents\skills`。junction 不可用时回退为复制；已有非链接目录会先移动到带时间戳的
备份目录，不会直接删除。强制复制可加 `-Copy`。

安装后重启 Codex，或在 `C:\Trading_analysis` 开启新对话：

- 输入 `/skills`，选择对应 skill；
- 或直接输入 `$bottom-fishing`、`$stock-diagnostic`、`$weekly-ashare-rank`，后接参数。

Codex 原生 skill 不是旧式的自定义 `/skill-name` 命令；`/skills` 是原生 slash 入口，`$skill-name` 是
精确显式调用语法。

验证安装：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" install-check
```

## 运行纪律

- 每次先跑共享 `baseline` hash 门禁，保证业务引擎、权重与 renderer 未被迁移层改写。
- 行情联网被沙箱阻断时走 Codex 的精确权限申请，不得用 `--no-verify`、`--no-notices` 绕过检查。
- 新闻/公告结论必须带来源 URL、发布日期和北京时间检索日期；无证据就写“无证据”。
- 最终 JSON 必须包含 `codex_audit`，执行一次反方挑战和一次审计官复核。
- HTML 必须经独立验收器验证；任何失败均标记“未通过”，不得发布为最终报告。
- A 股 T/T+1、交易日和时间戳一律使用原引擎输出的北京时间口径。

## 新增 skill

在本目录新建 `<skill-name>/SKILL.md`，使用有效 YAML frontmatter（`name`、具体 `description`），并添加
`agents/openai.yaml`。确定性逻辑放本地脚本，主观判断写成固定 rubric；随后重跑安装脚本并开启新对话。
如果新增共享工具，应放在本目录上一层，避免被安装器误认作 skill。
