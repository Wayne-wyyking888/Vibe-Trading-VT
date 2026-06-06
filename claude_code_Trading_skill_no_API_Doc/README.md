# Claude Code 交易 Skill 系统（0 API · 纯原生）

这个目录是一套**自管理的 Claude Code 交易 skill 库**。目标：

1. 你随时能写一个新 skill；
2. 装一下，之后**任意新对话**里直接 `/<skill名>` 就能用；
3. **完全不需要任何 API key**——Claude Code 自己就是大脑，只配合本机 Python 脚本 +
   自带 WebSearch；
4. 所有源文件都在本 repo 内，可同步到 GitHub。

---

## 它为什么不需要 API

Vibe-Trading 原生的 swarm（`agent/src/swarm/...`）是"每个 agent = 一个外部 LLM"，
必须配 OpenRouter/OpenAI 等 API key 才能跑。

本系统反过来：**Claude Code（你正在用的这个、订阅已付费的对话）就是那个 LLM**。
每个 skill 的 `SKILL.md` 是写给 Claude Code 的"作业流程"，Claude Code 读完后亲自执行
——需要数据就跑目录里的 Python 脚本（免费公开行情接口），需要资讯就用自带 WebSearch，
需要判断/辩论/排名就自己来。全程 0 API、0 额外花费。

```
普通 swarm:   你 → vibe-trading → 起N个进程 → 每个进程 HTTP 调外部LLM（要API key）
本系统:       你 → /skill → Claude Code 读 SKILL.md → 自己执行(跑脚本+WebSearch+推理)  ← 0 API
```

---

## 目录结构

```
claude_code_Trading_skill_no_API_Doc/
├── README.md              # 本文件（系统说明 + 怎么加 skill）
├── install_skills.ps1     # 一键安装/更新：把每个 skill 映射进 ~/.claude/skills
└── <skill-name>/
    ├── SKILL.md           # 【必需】给 Claude Code 的执行流程，含 YAML frontmatter
    ├── DOC.md             # 【建议】这个 skill 的完整文档
    └── *.py               # 【可选】这个 skill 用到的 Python 引擎/工具
```

现有 skill：
- **weekly-ashare-rank** — 周度A股选股排名（量化因子 + 消息催化剂 + 风险三方辩论）。
- **stock-diagnostic** — 单只A股持仓深度诊断（给代码+成本价，5-agent 辩论：技术/基本面/消息中英文/资金板块/风险裁决 → 综合裁定 + 结合成本的加仓/持有/减仓/止损建议）。复用 weekly-ashare-rank 引擎做数据客户端。

---

## 安装 / 让 skill 生效

Claude Code 从 `~/.claude/skills/<name>/SKILL.md`（用户级，全局任意对话可用）发现 skill。
本目录是**源**，安装脚本把每个 skill 软链接（junction）到那里：

```powershell
# 在本目录下运行（PowerShell）
powershell -ExecutionPolicy Bypass -File "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\install_skills.ps1"
```

- 默认用 **目录 junction**（软链接）：源文件改了，安装处实时同步，单一真相源。
- junction 建不了时自动回退为**复制**（改完源需重跑脚本同步）。
- 装完**重启 Claude Code / 开新对话**，输入 `/` 就能看到 `weekly-ashare-rank`。

验证：
```powershell
Get-ChildItem "$env:USERPROFILE\.claude\skills"
```

---

## 怎么写一个新 skill（3 步）

1. 在本目录新建文件夹 `my-skill/`，放一个 `SKILL.md`：
   ```markdown
   ---
   name: my-skill
   description: 一句话说清这个 skill 干什么、什么时候触发（描述要具体，Claude Code 靠它判断是否调用）。
   ---

   # 标题
   ## 执行步骤（Claude Code 按这个做）
   1. ...（要数据就写"跑 python xxx.py"；要资讯就写"用 WebSearch 查..."）
   2. ...
   ## 输出格式
   ## 硬性纪律（0 API、真实数据、不编造数字...）
   ```
2. 需要本机算力/数据就在同文件夹加 `.py` 脚本，并在 SKILL.md 里用**绝对路径**调用它。
3. 跑 `install_skills.ps1`，开新对话 `/my-skill`。

### 写 SKILL.md 的要点
- **frontmatter 的 `description` 很关键**：写清楚"做什么 + 何时触发 + 关键词"，
  Claude Code 据此决定要不要在对话里自动调用这个 skill。
- 流程要**可执行、可复现**：把确定性计算交给 Python 脚本，把判断/资讯交给 Claude Code。
- 行情/网络请求在 Windows 上需 `dangerouslyDisableSandbox: true`（沙箱默认拦外网）。
- 明确"0 API、只用真实数据、不得编造数字"的纪律，避免 Claude Code 用训练记忆里的旧数。

---

## 设计约定
- **单一真相源**：源永远在本 repo 目录；`~/.claude/skills` 只是 junction 镜像。
- **可同步**：本目录随 repo 进 GitHub（注意 `.py` 引擎、`.md` 文档都要提交）。
- **免费数据**：优先公开免费行情（东方财富/新浪/腾讯/yfinance/OKX），不引入需付费 token 的源。
- **跨源容错**：网络源都做多镜像 + 跨源回退，扛临时限流。
