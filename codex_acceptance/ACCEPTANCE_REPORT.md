# Codex 交易 Skill 迁移验收报告

验收日期口径：原引擎北京时间；本次联网数据 T=2026-07-17。

## 结论

迁移实现：**通过**。九个业务核心文件 hash 未改变；三个 Codex skill 已安装并通过格式、历史工件、
严格正负路径、临时重渲染、联网原始引擎和跨源验价检查。

本报告验证的是迁移 workflow。烟测产物不是经过当期完整网页研究后的投资最终报告，不得当作买卖建议。
新对话实际生成的最终报告仍必须逐次通过 `codex_audit` 与 HTML 强门禁。

## Codex 触发方式

- `/skills` → 选择 `bottom-fishing`、`stock-diagnostic` 或 `weekly-ashare-rank`；
- 或 `$bottom-fishing`、`$stock-diagnostic ...`、`$weekly-ashare-rank ...`。

Codex 原生不提供旧式直接 `/bottom-fishing` 自定义命令；这是宿主交互语法差异，不是 skill 功能降级。

## 安装结果

目标：`C:\Trading_analysis\.agents\skills`。

三个目标均为 junction，指向唯一真相源。`install-check`：15项通过。skill-creator 官方快速校验：三项均 `Skill is valid!`。
新对话或重启 Codex 后重新发现。

## 自动验收结果

| 门禁 | 结果 | 检查数 |
|---|---:|---:|
| 不可变核心 hash + 可变权重 schema | PASS | 41 |
| 历史工件/当前公式/内存负例 | PASS | 295 |
| 严格最终路径、证据、公式、gate、反方/审计与失败注入 | PASS | 618 |
| 三类当前 JSON 临时重渲染且生产 JSON 零写入 | PASS | 123 |
| Codex 安装发现 | PASS | 15 |
| Agent⓪ 联网闸门重算 | PASS | 16 |
| stock 原始联网引擎 | PASS | 16 |
| weekly 原始全市场联网引擎 | PASS | 289 |
| bottom 原始全市场联网引擎 + HTML | PASS | 122 |

历史工件：迁移前 56 个 HTML 与 14 个 JSON 均保留并可读取；本次 stock 烟测按原引擎设计新增一份
可比性 sidecar，因此当前 JSON 为15份。正常新增报告不再被错误当成回归，已有基线数量不得减少。

## 联网烟测

### bottom-fishing

- 完整扫描 490 只，未缩池；T=2026-07-17；底部区255只；6只过线；实际耗时约8分37秒。
- 公式/T/双路径/ATR/冷却/HTML 等122项通过。
- JSON：`C:\Trading_analysis\data\codex_smoke\bottom\bottom_latest.json`
- HTML：`C:\Trading_analysis\data\codex_smoke\bottom\reports\bottom_cn_2026-07-19_06-52-35.html`
- 影子日志：`C:\Trading_analysis\data\codex_smoke\bottom\bottom_shadow_log.jsonl`

### stock-diagnostic

- 代码600519、无成本、T+20、默认权重模式烟测；T=2026-07-17；F10、资金流、价位矩阵均成功。
- 原始引擎16项通过；独立验价腾讯×新浪均为1253.0，偏差0%。
- JSON：`C:\Trading_analysis\data\codex_smoke\diag_600519.json`
- 验价：`C:\Trading_analysis\data\codex_smoke\price_600519.json`
- 原引擎可比性 sidecar：
  `C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\stock-diagnostic\reports\diag_600519_cn_2026-07-19_06-27-56.json`

### weekly-ashare-rank

- 默认 pool=400/top=20、N=3；免费源实际初筛252只、完成250只历史因子，Top20 全部完成跨源验价与公告/F10。
- T=2026-07-17；Agent⓪=0分/观望/总仓15%；289项通过。
- 市场闸门：`C:\Trading_analysis\data\codex_smoke\market_gate_latest.json`
- JSON：`C:\Trading_analysis\data\codex_smoke\rank_latest.json`

## 不完全相同但不构成功能降级的地方

1. 显式调用语法：旧宿主可直接 `/skill-name`；Codex 原生为 `/skills` 选择或 `$skill-name`。
2. 核心 Python 的历史 stdout/注释仍可能出现旧品牌词。为保持 hash 与业务基线不变，不改核心；最终 HTML 在验收前机械归一为 Codex。
3. 免费源延迟可变化。本次 bottom 实测8分37秒，高于旧文档2–4分钟；流程没有因此缩池、并发改写或跳校验。

除以上宿主语法、不可变核心历史文案和免费源时延外，没有发现量化计算、阈值、gate、排序、价位、review/recheck、
JSON schema 或 HTML 语义上的已知降级。

## 复验命令

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" baseline
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" fixtures
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" self-test
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" rerender-test
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" install-check
```
