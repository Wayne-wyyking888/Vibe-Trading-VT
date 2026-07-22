# -*- coding: utf-8 -*-
"""CatBoost 抄底打分影子实验: purged前推CV + 单调约束 + holdout + 分regime三张表。
纯研究, 只读 panel.parquet, 不碰任何 skill。对比基线=引擎规则(等选择率)。"""
import pathlib
import numpy as np, pandas as pd
from catboost import CatBoostClassifier

ROOT = pathlib.Path(r"C:\Trading_analysis\research\bottom_ml")
TH_TOTAL, TH_STOCK, TH_ATR = 18.0, 15.0, 4.0
EMBARGO = 20               # 交易日, = MAX_HOLD, 清洗标签重叠
HOLDOUT_START = "2026-05"  # 5/6/7 作 holdout

FEATS = ["dd60", "pos60", "atr", "rsv", "ret5", "volx", "downstk",
         "def_days", "idx_rsv", "idx_chg1", "defensive"]   # 去dd250(250日窗会砍掉2024前10月)
MONO = {"atr": -1, "downstk": -1, "dd60": 1, "defensive": 1}   # 只锁明确符号(反指标铁律)
CB_PARAMS = dict(iterations=400, depth=4, learning_rate=0.05, l2_leaf_reg=6.0,
                 loss_function="Logloss", random_seed=7, verbose=False,
                 monotone_constraints=[MONO.get(f, 0) for f in FEATS])


def rule_qualified(df: pd.DataFrame) -> pd.Series:
    q = ((df.mkt_def & (df.score >= TH_TOTAL)) | (~df.mkt_def & (df.stock_score >= TH_STOCK))) & (df.atr <= TH_ATR)
    return q


def stats(sub: pd.DataFrame) -> tuple:
    if len(sub) == 0:
        return (0, np.nan, np.nan)
    return (len(sub), (sub.outcome == "win").mean() * 100, (sub.outcome == "stop").mean() * 100)


def pick_topN(df: pd.DataFrame, pred: np.ndarray, N: int) -> pd.DataFrame:
    if N <= 0:
        return df.iloc[[]]
    order = np.argsort(-pred)
    return df.iloc[order[:N]]


def eval_block(name, df, pred):
    """在一个eval集上: base / 规则 / ML(等选择率N=规则数) / ML(top10%)。"""
    n_all, w_all, s_all = stats(df)
    rq = rule_qualified(df)
    r = df[rq.values]
    n_r, w_r, s_r = stats(r)
    ml_eq = pick_topN(df, pred, n_r)
    n_e, w_e, s_e = stats(ml_eq)
    N10 = max(1, int(len(df) * 0.10))
    ml10 = pick_topN(df, pred, N10)
    n_t, w_t, s_t = stats(ml10)
    return dict(block=name, n_all=n_all, base_win=w_all, base_stop=s_all,
                rule_n=n_r, rule_win=w_r, rule_stop=s_r,
                mlEq_n=n_e, mlEq_win=w_e, mlEq_stop=s_e,
                ml10_n=n_t, ml10_win=w_t, ml10_stop=s_t)


def fmt(rows, title):
    print(f"\n### {title}")
    print("| 区块 | 底部区n | base胜 | base雷 | 规则n | 规则胜 | 规则雷 | ML等量n | ML胜 | ML雷 | ML top10%胜 | top10%雷 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        f = lambda x: f"{x:.1f}" if isinstance(x, float) and not np.isnan(x) else ("-" if isinstance(x, float) else str(x))
        print(f"| {r['block']} | {r['n_all']} | {f(r['base_win'])} | {f(r['base_stop'])} | "
              f"{r['rule_n']} | {f(r['rule_win'])} | {f(r['rule_stop'])} | "
              f"{r['mlEq_n']} | {f(r['mlEq_win'])} | {f(r['mlEq_stop'])} | "
              f"{f(r['ml10_win'])} | {f(r['ml10_stop'])} |")


def train_cb(tr):
    m = CatBoostClassifier(**CB_PARAMS)
    m.fit(tr[FEATS].values, (tr.outcome == "win").astype(int).values)
    return m


def main():
    p = pd.read_parquet(ROOT / "panel.parquet").copy()
    p["defensive"] = p["defensive"].astype(int)
    p = p.sort_values("d").reset_index(drop=True)
    p[FEATS] = p[FEATS].apply(pd.to_numeric, errors="coerce")
    p = p.dropna(subset=FEATS).reset_index(drop=True)
    dates = np.array(sorted(p.d.unique()))
    print(f"[model] 样本 {len(p)}  日期 {dates[0]}→{dates[-1]}  "
          f"holdout({HOLDOUT_START}+)={ (p.d>=HOLDOUT_START).sum() }")

    train_pool = p[p.d < HOLDOUT_START].reset_index(drop=True)
    hold = p[p.d >= HOLDOUT_START].reset_index(drop=True)
    tp_dates = np.array(sorted(train_pool.d.unique()))

    # ---------- 表①: Purged 前推 CV (因果 OOS) ----------
    oof = np.full(len(p), np.nan)          # 存回全表(按索引), 供regime表用
    p_idx = {d: i for i, d in enumerate(p.d.values)}  # not unique; use mask instead
    start = int(len(tp_dates) * 0.45)      # 前45%做初始训练, 之后前推
    step = 15
    cv_rows = []
    fold = 0
    i = start
    while i < len(tp_dates):
        val_dates = set(tp_dates[i:i + step])
        val_start = tp_dates[i]
        # purge+embargo: 训练日必须 < val_start 且其标签窗(+20交易日)不越界 → 训练日index <= i-1-EMBARGO
        tr_cut = i - EMBARGO
        if tr_cut < 30:
            i += step; continue
        tr_dates = set(tp_dates[:tr_cut])
        tr = train_pool[train_pool.d.isin(tr_dates)]
        va = p[p.d.isin(val_dates)]
        if len(va) < 20 or len(tr) < 200:
            i += step; continue
        m = train_cb(tr)
        pred = m.predict_proba(va[FEATS].values)[:, 1]
        # 写回oof
        va_mask = p.d.isin(val_dates).values
        oof[va_mask] = pred
        fold += 1
        cv_rows.append(eval_block(f"fold{fold} {tp_dates[i][:7]}~", va.reset_index(drop=True), pred))
        i += step
    # CV 汇总(把所有fold的val拼起来按等量选择聚合)
    cv_mask = ~np.isnan(oof) & (p.d < HOLDOUT_START).values
    cv_all = p[cv_mask].reset_index(drop=True)
    cv_pred = oof[cv_mask]
    agg = eval_block("CV合计(OOS)", cv_all, cv_pred)
    fmt([agg] + cv_rows, "表① Purged前推CV — OOS(因果, embargo=20交易日)  [ML等量 vs 引擎规则]")

    # ---------- 表②: Holdout 2026-05/06/07 ----------
    tp_sorted = np.array(sorted(train_pool.d.unique()))
    # 训练用全部 train_pool(其标签窗天然在holdout前, 已被数据边界隔离; 再砍尾部EMBARGO日防泄漏)
    cut_d = tp_sorted[-EMBARGO] if len(tp_sorted) > EMBARGO else tp_sorted[-1]
    tr_final = train_pool[train_pool.d < cut_d]
    m_final = train_cb(tr_final)
    hpred = m_final.predict_proba(hold[FEATS].values)[:, 1]
    oof_hold = hpred
    h_row = eval_block(f"Holdout {HOLDOUT_START}~07", hold, hpred)
    # 按月拆
    hrows = [h_row]
    for mth in sorted(hold.month.unique()):
        sub = hold[hold.month == mth].reset_index(drop=True)
        sp = hpred[(hold.month == mth).values]
        hrows.append(eval_block(mth, sub, sp))
    fmt(hrows, "表② Holdout(样本外·从未参与训练/CV) [ML等量 vs 引擎规则]")
    print(f"    (训练集={len(tr_final)}样本, 截至{cut_d}前; holdout={len(hold)}样本)")

    # ---------- 表③: 分 regime ----------
    # 用OOS预测(CV的oof + holdout的hpred)拼全表, 每个时间块报 base/规则/ML
    full_pred = oof.copy()
    full_pred[(p.d >= HOLDOUT_START).values] = oof_hold  # holdout段填模型预测
    def regime_of(d):
        y, mo = int(d[:4]), int(d[5:7])
        if y == 2024:
            return "2024H1" if mo <= 6 else "2024H2"
        if y == 2025:
            return "2025H1" if mo <= 6 else "2025H2"
        if d >= HOLDOUT_START:
            return "2026-05~07(holdout)"
        return "2026-01~04"
    p["regime"] = p.d.map(regime_of)
    reg_rows = []
    for rg in ["2024H1", "2024H2", "2025H1", "2025H2", "2026-01~04", "2026-05~07(holdout)"]:
        mask = (p.regime == rg).values & ~np.isnan(full_pred)
        sub = p[mask].reset_index(drop=True)
        if len(sub) < 10:
            reg_rows.append(dict(block=rg + "(OOS不足)", n_all=int((p.regime==rg).sum()),
                                 base_win=np.nan, base_stop=np.nan, rule_n=0, rule_win=np.nan,
                                 rule_stop=np.nan, mlEq_n=0, mlEq_win=np.nan, mlEq_stop=np.nan,
                                 ml10_n=0, ml10_win=np.nan, ml10_stop=np.nan))
            continue
        reg_rows.append(eval_block(rg, sub, full_pred[mask]))
    fmt(reg_rows, "表③ 分regime(每格均用OOS预测) — 检验市况依赖 [ML等量 vs 引擎规则]")

    # ---------- 反向泛化诊断: 好市况训练 → 2024毒月测试 ----------
    good = p[(p.d >= "2025-01") & (p.d < HOLDOUT_START)]
    bad24 = p[p.d.str[:4] == "2024"].reset_index(drop=True)
    if len(good) > 300 and len(bad24) > 50:
        mg = train_cb(good)
        bp = mg.predict_proba(bad24[FEATS].values)[:, 1]
        diag = eval_block("2024全年(反向测试)", bad24, bp)
        fmt([diag], "诊断 反向泛化: 训练=2025~2026Q1(好市况) → 测试=2024(阴跌熊市)  [ML能否迁移到毒月]")

    # 特征重要度(holdout模型)
    imp = pd.Series(m_final.get_feature_importance(), index=FEATS).sort_values(ascending=False)
    print("\n### CatBoost 特征重要度(holdout模型)")
    print("  " + "  ".join(f"{k}={v:.1f}" for k, v in imp.items()))


if __name__ == "__main__":
    main()
