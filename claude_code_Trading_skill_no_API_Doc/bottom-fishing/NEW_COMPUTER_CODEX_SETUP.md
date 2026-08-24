# bottom-fishing 新电脑 Codex 安装、迁移与完整验收协议

> 本文件是给新电脑上的 Codex 直接执行的安装 runbook，不是普通阅读材料。
> 用户只要让 Codex“完整读取并执行本文件”，Codex 就应从阶段 0 开始连续完成安装、检查、联网烟测和交付；
> 不得跳步，不得把半成品称为 READY，也不得让用户代做本文件中可由 Codex 完成的检查。

## 0. 目标、适用范围与诚实边界

目标是在一台新的 **Windows 11** 电脑上，把本仓库安装成与基准电脑大体一致的 Codex 工作环境，使用户之后可在
新对话中用 `/skills` 选择 `bottom-fishing`，或直接输入 `$bottom-fishing`，完整执行：

1. 不可变量化引擎与原始报告；
2. Agent② 六维个股错杀裁定；
3. Agent③ 五域风险、五路市场发现、八行业族、八类预测输入、重大事件预期与发布后对账；
4. T 日与 T 后信息隔离；
5. ETF 持仓与走势相似度、跨源验价、审计附加、品牌处理；
6. 所有硬门禁、一次性自修复和最终 HTML 验收；
7. 影子日志、五交易日冷却和 `--review` 连续性。

这里的“大体一致”是指：**相同代码版本、相同量化规则、相同状态历史、相同数据源优先级、相同模型档位、相同
workflow 和相同硬验收合同**。以下内容无法承诺逐字节相同：

- 免费行情源可能修订前复权数据、限流或切换备用源；
- 全市场成交额前 600 的实时快照和 6 小时缓存会随运行时点变化；
- ETF 持仓端点按规定每次在线刷新，不使用旧缓存冒充成功；
- 网页内容、搜索排序、宏观实际值和突发新闻会变化；
- Codex 的网页研究和文字表达不是确定性程序；
- HTML 的生成时间戳必然不同。

因此，本协议保证的是“正常条件下完整跑通且不漏环节”，不是在两个时点生成完全相同的股票、网页证据或 HTML
字节。遇到账号无对应模型、无网页检索能力、公司防火墙封锁全部公开数据源、Git 无权限或系统不允许写入目标目录时，
Codex 必须明确报告真实阻断，不能伪造 PASS。

## 1. 基准环境快照

本文件创建时的已验收基准如下：

| 项目 | 基准值 |
|---|---|
| 操作系统 | Windows 11 |
| 工作区根目录 | `C:\Trading_analysis` |
| 仓库目录 | `C:\Trading_analysis\Vibe-Trading-VT` |
| Git remote | `https://github.com/Wayne-wyyking888/Vibe-Trading-VT.git` |
| 基准提交 | `81167ae010515e5cb289f6a08dde8e6b2b8e7b53` |
| Codex CLI | `0.149.0-alpha.4.1` |
| Codex 模型 | `gpt-5.6-sol` |
| reasoning effort | `xhigh` |
| Python | `3.12.10` |
| pandas | `3.0.3` |
| numpy | `2.4.6` |
| requests | `2.34.2` |
| 基线门禁 | 41 checks / 0 errors / 0 warnings |
| 当前最终报告验收 | 3966 checks / 0 errors / 0 warnings |

基准提交只是本文件创建时的锚点。若远端 `main` 后续有合法更新，新电脑应使用最新、已推送且能通过
`acceptance.py baseline` 的提交；不得为了匹配旧锚点而丢弃更新后的生产状态。

## 2. Codex 执行纪律

新电脑 Codex 在执行本协议时必须遵守：

1. 全程使用 PowerShell；路径中的实际目录名是 `Trading_analysis`，下划线前没有反斜杠转义字符。
2. 先做只读检查，再创建目录、安装包或 junction；不得删除未知文件，不得 `git reset --hard`。
3. 若仓库已存在且工作树不干净，保留用户改动并停止覆盖；不得擅自清理。
4. 不修改九个 baseline 锁定的业务核心文件，不修改量化阈值、数据源顺序或 renderer。
5. 安装和烟测期间不得运行研究脚本；`scripts/research/` 只用于用户明确要求的重校准。
6. 不调用付费行情 API、MCP 或外部 agent。正式裁定只使用 Codex 自带网页检索和公开来源。
7. 长任务无 stdout 不等于失败。必须沿用同一进程/session 等待，禁止并发启动第二个 bottom 引擎。
8. 所有 PASS 条件满足前，不得回复“安装完成”“可以用了”或发布最终报告。

OpenAI 官方说明：Codex 从当前工作目录到仓库根扫描 `.agents/skills`，并支持 symlinked skill folder。参考：
`https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills`。

## 3. 阶段 A：确认 Codex 能力与权限

### A1. Codex 自检

运行：

```powershell
codex --version
codex doctor
```

要求：

- Codex CLI 或桌面版必须支持本地 skills、PowerShell、长任务等待和网页检索；
- 用户已登录；
- 工作区必须打开为 `C:\Trading_analysis`，不要只打开内层 `Vibe-Trading-VT`；
- Codex 对 `C:\Trading_analysis` 有读写权限，对公开行情和官方网页有联网能力；
- 模型优先使用 `gpt-5.6-sol`，reasoning effort 使用 `xhigh`。

检查用户级 `config.toml` 时只合并必要设置，不得覆盖其他配置。若当前 Codex 版本仍支持以下键，可采用：

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "xhigh"
```

修改 Codex 配置后按产品要求重启 Codex。若账号没有该模型，使用账号中能力最高、支持长上下文与网页工具的 Codex
模型，并在最终交付中标明偏差；若连完整网页检索都不可用，则正式 workflow 不具备发布条件。

### A2. 网页检索能力探针

Codex 必须实际执行一次网页搜索并打开一个公开页面，确认能返回 URL、页面标题和发布日期/访问日期。只看到搜索摘要、
无法打开来源页，不算通过。不得用 shell 搜索库、MCP 或付费 API 替代 Codex 网页检索。

**阶段 A PASS：** Codex、shell、网页检索和工作区写权限均可用；否则报告具体阻断并停止。

## 4. 阶段 B：取得唯一真相源

先定义固定路径：

```powershell
$BottomWorkspaceRoot = 'C:\Trading_analysis'
$BottomRepoRoot = 'C:\Trading_analysis\Vibe-Trading-VT'
$BottomRemote = 'https://github.com/Wayne-wyyking888/Vibe-Trading-VT.git'
```

若仓库不存在：

```powershell
New-Item -ItemType Directory -Force -Path $BottomWorkspaceRoot | Out-Null
git clone $BottomRemote $BottomRepoRoot
```

若仓库已经存在，先核对 remote，不得在错误仓库执行：

```powershell
git -C $BottomRepoRoot rev-parse --show-toplevel
git -C $BottomRepoRoot remote -v
git -C $BottomRepoRoot status --short
```

只有工作树干净时才同步：

```powershell
git -C $BottomRepoRoot fetch origin
git -C $BottomRepoRoot switch main
git -C $BottomRepoRoot pull --ff-only origin main
git -C $BottomRepoRoot rev-parse HEAD
git -C $BottomRepoRoot rev-parse origin/main
```

要求 `HEAD == origin/main`。迁移旧电脑时，必须先确认旧电脑已经提交并推送最新生产状态，然后停止旧电脑的定时/手工
运行；两个电脑不得同时作为生产写入者。

以下文件必须全部由 Git 跟踪且存在：

```text
claude_code_Trading_skill_no_API_Doc/bottom-fishing/SKILL.md
claude_code_Trading_skill_no_API_Doc/bottom-fishing/agents/openai.yaml
claude_code_Trading_skill_no_API_Doc/bottom-fishing/bottom_fishing.py
claude_code_Trading_skill_no_API_Doc/bottom-fishing/references/WEB_EVIDENCE_PROTOCOL.md
claude_code_Trading_skill_no_API_Doc/bottom-fishing/references/TOXIC_RISK_WARNING_PROTOCOL.md
claude_code_Trading_skill_no_API_Doc/bottom-fishing/references/AGENT3_SECTOR_MAPPING_PROTOCOL.md
claude_code_Trading_skill_no_API_Doc/bottom-fishing/scripts/finalize_bottom.py
claude_code_Trading_skill_no_API_Doc/bottom-fishing/scripts/normalize_bottom_audit.py
claude_code_Trading_skill_no_API_Doc/bottom-fishing/state/bottom_latest.json
claude_code_Trading_skill_no_API_Doc/bottom-fishing/state/bottom_adjudication.json
claude_code_Trading_skill_no_API_Doc/bottom-fishing/state/bottom_shadow_log.jsonl
claude_code_Trading_skill_no_API_Doc/bottom-fishing/state/codex_price_verification.json
claude_code_Trading_skill_no_API_Doc/weekly-ashare-rank/ashare_weekly_rank.py
claude_code_Trading_skill_no_API_Doc/weekly-ashare-rank/universe_seed.txt
codex_acceptance/acceptance.py
codex_acceptance/baseline_manifest.json
codex_acceptance/bottom_etf.py
codex_acceptance/run_engine.py
codex_acceptance/verify_prices.py
codex_acceptance/JUDGE_SCHEMA.md
```

使用以下命令核对四个生产状态确实在 Git 中，而不是仅存在于旧电脑：

```powershell
git -C $BottomRepoRoot ls-files -- `
  'claude_code_Trading_skill_no_API_Doc/bottom-fishing/state/bottom_latest.json' `
  'claude_code_Trading_skill_no_API_Doc/bottom-fishing/state/bottom_adjudication.json' `
  'claude_code_Trading_skill_no_API_Doc/bottom-fishing/state/bottom_shadow_log.jsonl' `
  'claude_code_Trading_skill_no_API_Doc/bottom-fishing/state/codex_price_verification.json'
```

**阶段 B PASS：** remote 正确、工作树干净、`HEAD == origin/main`、上述源文件和四个状态文件齐全。

## 5. 阶段 C：安装并锁定 Python 运行时

生产 workflow 的最小第三方依赖只有 `numpy`、`pandas`、`requests` 及其传递依赖；不需要安装付费 API SDK，
也不需要为了日常扫描安装研究脚本使用的 CatBoost/Parquet 环境。

首先检查 bare `python`。正式 skill 的命令使用的是 `python`，所以只确认 `py -3.12` 可用还不够：

```powershell
python --version
python -c "import sys; print(sys.executable); print(sys.version)"
```

要求 `python` 指向 64 位 CPython 3.12；与基准最接近的是 3.12.10。若未安装，优先通过 python.org 官方安装器或
Windows Package Manager 安装 Python 3.12，并启用 `Add Python to PATH`。安装或 PATH 变化后重启 Codex，再继续本协议。

在 bare `python` 对应的环境中安装基准版本：

```powershell
python -m pip install --upgrade `
  "numpy==2.4.6" `
  "pandas==3.0.3" `
  "requests==2.34.2" `
  "certifi==2026.5.20" `
  "charset-normalizer==3.4.7" `
  "idna==3.18" `
  "urllib3==2.7.0" `
  "python-dateutil==2.9.0.post0" `
  "six==1.17.0" `
  "tzdata==2026.2"
```

随后核对：

```powershell
python -m pip check
python -c "import sys,pandas,numpy,requests; print(sys.executable); print(sys.version); print('pandas='+pandas.__version__); print('numpy='+numpy.__version__); print('requests='+requests.__version__)"
```

不要主动安装 `pyarrow`。若系统已有损坏或被应用控制策略拦截的可选 `pyarrow.compute`，`run_engine.py` 会把它视为
未安装；生产引擎不使用 Arrow I/O。

**阶段 C PASS：** bare `python` 为 CPython 3.12，三项核心包版本正确，`pip check` 为 0。

## 6. 阶段 D：建立 Codex skill junction

唯一真相源保留在仓库内；`C:\Trading_analysis\.agents\skills` 只放 junction，禁止复制出第二套可编辑副本。

```powershell
$BottomSkillsSource = Join-Path $BottomRepoRoot 'claude_code_Trading_skill_no_API_Doc'
$BottomSkillsInstall = Join-Path $BottomWorkspaceRoot '.agents\skills'
New-Item -ItemType Directory -Force -Path $BottomSkillsInstall | Out-Null

$BottomSkillNames = @('bottom-fishing', 'stock-diagnostic', 'weekly-ashare-rank')
foreach ($BottomSkillName in $BottomSkillNames) {
    $BottomTarget = Join-Path $BottomSkillsSource $BottomSkillName
    $BottomLink = Join-Path $BottomSkillsInstall $BottomSkillName
    if (-not (Test-Path -LiteralPath $BottomTarget)) {
        throw "唯一真相源不存在: $BottomTarget"
    }
    if (Test-Path -LiteralPath $BottomLink) {
        $BottomItem = Get-Item -LiteralPath $BottomLink -Force
        $BottomActualTarget = [IO.Path]::GetFullPath([string]$BottomItem.Target)
        $BottomExpectedTarget = [IO.Path]::GetFullPath($BottomTarget)
        if ($BottomItem.LinkType -ne 'Junction' -or $BottomActualTarget -ine $BottomExpectedTarget) {
            throw "安装位置已有非预期文件/链接，保留现场并停止: $BottomLink"
        }
    } else {
        New-Item -ItemType Junction -Path $BottomLink -Target $BottomTarget | Out-Null
    }
}
```

不要自动删除错误链接；若目标不符，先报告完整路径，得到用户确认后再处理。

运行安装验收：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" install-check
```

预期：`[PASS] install: checks=15 errors=0 warnings=0`。

关闭并重新打开 Codex，工作区选择 `C:\Trading_analysis`。在新对话运行 `/skills`，必须能看到：

- `bottom-fishing`
- `stock-diagnostic`
- `weekly-ashare-rank`

然后显式提及 `$bottom-fishing`，确认 Codex 加载的路径最终指向本仓库唯一真相源。

**阶段 D PASS：** junction 目标正确、15 项安装检查通过、重启后的 `/skills` 能发现三个 skill。

## 7. 阶段 E：目录、状态与不可变基线门禁

创建可重建缓存目录；生产状态仍只保存在 skill 的 `state/`：

```powershell
New-Item -ItemType Directory -Force -Path 'C:\Trading_analysis\data\cache\ashare_weekly' | Out-Null
New-Item -ItemType Directory -Force -Path 'C:\Trading_analysis\data\codex_smoke\bottom' | Out-Null
```

先跑不可变基线：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" baseline
```

预期：`[PASS] baseline: checks=41 errors=0 warnings=0`。失败时不得继续，必须检查是否 checkout 错误、文件被换行/安全
软件改写、仓库版本和 `baseline_manifest.json` 不匹配；不得通过修改 manifest 掩盖变化。

验证 Git 中自带的最新 bottom 结构化状态和完整 Agent②/③审计合同：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate `
  --skill bottom-fishing `
  --json "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing\state\bottom_latest.json" `
  --require-bottom-search
```

再做入口/import 检查：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\run_engine.py" bottom -- --help
```

注意：不要在 fresh clone 上把 `acceptance.py fixtures`、`self-test` 或 `rerender-test` 当成必过安装项；这些三-skill
历史回归命令可能依赖旧电脑未进 Git 的其他 skill 历史工件。bottom 的迁移门禁以上述 baseline、当前状态 validate、
入口检查和下一阶段隔离烟测为准。

**阶段 E PASS：** baseline、当前状态 validate、入口/import 检查全部返回 0。

## 8. 阶段 F：隔离联网烟测，不污染生产状态

烟测使用 `C:\Trading_analysis\data\codex_smoke\bottom`，不得直接用正式 `bottom` entry。运行：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\run_engine.py" bottom-smoke --
```

该命令仍会联网拉取全市场快照和约数百只 K 线，可能持续十分钟以上。必须沿用原进程等待；调用层暂时无输出、超时或
返回可继续等待的 session/cell ID 时，继续等待同一 ID，禁止启动第二份烟测。

完成后，从 stdout 读取实际 HTML 绝对路径，并运行：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate-bottom-engine `
  --json "C:\Trading_analysis\data\codex_smoke\bottom\bottom_latest.json" `
  --html "<烟测 stdout 输出的实际 HTML 绝对路径>"
```

检查：

- T 是最近已收盘 A 股交易日；
- JSON 与 HTML 同一 T；
- `generated_at` 带 `+08:00`；
- 报告文件名是完整北京时间生成时点；
- 快照不是空表，K 线扫描正常完成；
- 没有并发 Python bottom 进程；
- 正式 `bottom-fishing/state/` 四个文件在烟测前后 Git diff 不变。

若主源失败但自动备用源成功，烟测可通过；若全部公开源被网络策略封锁，先切换网络或为相应域名申请放行，再沿用原
纪律重试。不得以伪造数据、跳过校验或复制旧烟测结果方式通过。

**阶段 F PASS：** 隔离烟测完整结束，`validate-bottom-engine` 返回 0，生产状态无变化。

## 9. 阶段 G：正式 `$bottom-fishing` workflow 的防漏清单

以后每次正式扫描，新电脑 Codex 必须先完整读取：

1. 本目录 `SKILL.md`；
2. `C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\JUDGE_SCHEMA.md`；
3. 无论候选数是否为 0，都完整读取：
   - `references/TOXIC_RISK_WARNING_PROTOCOL.md`
   - `references/AGENT3_SECTOR_MAPPING_PROTOCOL.md`
4. 有过线候选时，再完整读取 `references/WEB_EVIDENCE_PROTOCOL.md`。

不得用本文件的摘要代替上述原始协议。机械防漏顺序固定为：

- [ ] 运行 `acceptance.py baseline`；
- [ ] 确认没有另一台电脑或另一进程正在运行 bottom；
- [ ] 运行唯一一份 `run_engine.py bottom --` 并等待完成；
- [ ] 立即运行 `validate-bottom-engine`；
- [ ] 0 过线也不凑数，结论为空手；
- [ ] 有候选时，Agent②完成每票六类搜索、官方公告回扫、T 日跌因、名称/代码精确回溯、F10 每条 seed ledger；
- [ ] Agent②区分 `base_verdict_asof_t`、`post_t_safety_by_code` 和 `effective_verdict`；
- [ ] 每次都运行 Agent③，覆盖五域、五路发现、八行业族和八类预测输入；
- [ ] 每个重大事件完成新鲜度、phase、原市场交易日、北京时间可得时点和 A 股兑现状态；
- [ ] 扫描未来十个自然日重大排期，完成三路预期检索、指标 ledger 和发布后 reconciliation；
- [ ] 完成 `market_signals`、warning/delta、五域到 `sector_calls` 的全部双向处置，未解释项必须为 0；
- [ ] 精确命中候选行业时完成 `by_code.sector_context` 双向下沉；
- [ ] T 后信息只进运行时安全评估，不倒灌 T 日裁定；
- [ ] 写入同一 T 的 `state/bottom_adjudication.json`；
- [ ] 运行 `validate-bottom-search`，失败则读取全部错误、补网页证据/映射并重验；
- [ ] 运行 `scripts/finalize_bottom.py`，让自动发布器处理 adjudicate、ETF、验价、attach、augment、brand 和最终验收；
- [ ] ETF 必须当次在线刷新；`blocked/partial/数据获取失败` 不得发布；
- [ ] 最终 HTML 不得含禁止占位文本，候选卡必须有 ETF 和六维裁定区块；
- [ ] 最终 `acceptance.py validate ... --require-bottom-search` 必须返回 0；
- [ ] reports 中同一 T 最多只保留最新裁定版；
- [ ] 最终只向用户提供通过硬门禁的裁定版 HTML，不发布原始版或半成品。

搜索审计缺字段时，优先继续检索并补同一份审计；不得用 `no_reliable_consensus`、`neutral`、`not_applicable` 或删字段
绕过硬门禁。只有权限、账号能力或全部外部来源持续不可达等真实外部阻断才允许停下。

## 10. 运行时点、缓存与跨电脑一致性

为了让两个环境的输入尽量接近：

- 日常生产建议在 **北京时间 06:00–08:30** 运行：美国最新完整交易时段通常已可得，A 股尚未开盘；
- 避免北京时间 09:15–15:05 运行，盘中成交额前 600 快照会变化；
- 新电脑首次运行允许空缓存在线重建，不要用过期缓存假装成功；
- 普通生产不需要从旧电脑复制 `data/cache/ashare_weekly`；它可重建且不属于生产状态；
- 若专门比较同一 T 的量化输出，应使用同一代码提交、同一四个 state 输入、同一运行窗口和同一缓存快照；
- 当前生产引擎没有完全离线的行情/网页 snapshot replay，因此仍不能承诺逐字节复现。

时区不要求把 Windows 系统时区改成中国时区。引擎统一用 UTC+8 计算 T、缓存年龄和报告时间；必须保证系统 UTC 时钟
已通过 Windows 时间服务正确同步。

## 11. 生产状态交接与单写者纪律

以下四个文件共同决定最新结果、裁定、冷却历史和验价，必须作为一个事务看待：

```text
state/bottom_latest.json
state/bottom_adjudication.json
state/bottom_shadow_log.jsonl
state/codex_price_verification.json
```

迁移步骤：

1. 停止旧电脑的 bottom 运行；
2. 旧电脑确认最终验收通过；
3. 旧电脑只提交合法状态/协议变更，不夹带无关文件；
4. 推送远端；
5. 新电脑 `git pull --ff-only`；
6. 新电脑重新跑 baseline 和当前状态 validate；
7. 之后只允许新电脑作为生产写入者。

每次生产运行前都要 `git pull --ff-only`。运行后检查 `git status --short`；不要提交 `reports/*.html` 和外部 cache。若需要
跨电脑保存某一份完全相同的最终 HTML，应把通过验收的 HTML 作为单独 release artifact 保存并记录 SHA-256，而不是依赖
重新生成。

## 12. Codex 最终交付格式

只有 A–F 全部 PASS 后，安装 Codex 才能回复 READY。回复必须包含：

```text
bottom-fishing 新电脑环境：READY
Workspace: C:\Trading_analysis
Repo: C:\Trading_analysis\Vibe-Trading-VT
Commit: <HEAD SHA>
Remote parity: HEAD == origin/main
Codex: <version / model / reasoning effort>
Python: <executable / version>
Packages: pandas=<v>, numpy=<v>, requests=<v>
Skill discovery: PASS (15 checks)
Baseline: PASS (41 checks)
Tracked state validation: PASS (<实际 checks>)
Isolated live smoke: PASS (<T> / <实际 checks>)
Production state changed by smoke: NO
Web research capability: PASS
Single writer: <新电脑/交接状态>
Known non-determinism: live market, live web, ETF refresh, timestamps
Next command: 在新对话输入 $bottom-fishing
```

若任一项未通过，回复必须写 `NOT READY`、失败阶段、原始错误摘要、已尝试的安全修复和需要的外部条件；不得给出误导性的
“基本可以用”。

## 13. 用户在新电脑给 Codex 的一句话

```text
完整读取并严格执行 C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing\NEW_COMPUTER_CODEX_SETUP.md。请连续完成安装、junction、精确运行时、基线、状态验证和隔离联网烟测；能安全自修复的错误自行修复并重验。只有文档 A–F 全部 PASS 后才回复 READY，不要跳步、不要运行研究脚本、不要污染正式 state、不要让我代填检查结果。
```

