# -*- coding: utf-8 -*-
"""本地 embedding 算边：节点向量化 → 余弦相似度 → 每节点保留 top_k 条边。

这一步完全免费、纯本地。没有它就得让 LLM 判断"哪两个概念相关"，
既贵又不稳定（LLM 对相似度的判断一致性远不如余弦距离）。
"""
import numpy as np


class Embedder:
    """延迟加载的句向量模型。全流程只加载一次，传对象复用。"""

    def __init__(self, model_name="BAAI/bge-large-zh-v1.5", device="cuda"):
        self.model_name = model_name
        self.device = device
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer
        try:
            self._model = SentenceTransformer(self.model_name, device=self.device)
        except Exception:
            # GPU 不可用时静默降级到 CPU，比直接崩掉友好
            if self.device != "cpu":
                self._model = SentenceTransformer(self.model_name, device="cpu")
                self.device = "cpu"
            else:
                raise
        return self._model

    def encode(self, texts, batch_size=64):
        model = self._load()
        return model.encode(
            texts, normalize_embeddings=True,
            batch_size=batch_size, show_progress_bar=False,
        )

    @property
    def is_loaded(self):
        return self._model is not None


def node_text(n):
    """节点用于向量化的文本。label 权重天然更高（放前面）。"""
    return "%s：%s" % (n.get("label", ""), n.get("content", ""))


def build_edges(nodes, embedder, top_k=3, min_sim=0.60, same_subject=None,
                adaptive=True, target_density=None):
    """算边。

    nodes: [{"label","content", ...}]，下标即临时 id
    same_subject: 可选 list。传了就只连同科目的边（用于科内图）；跨科图反向用。

    adaptive: 自适应门槛。**这是踩过坑才加的**——bge 的相似度分布在不同讲次之间
              差异极大（同一批语料里，有的讲中位数 0.49、有的 0.69）。用固定的
              0.60 会导致某些讲连出 10+ 边/节点糊成一团，另一些讲只有 0.6 条稀稀
              拉拉。自适应按分布取分位数，让每讲密度趋于一致。
    """
    if len(nodes) < 2:
        return []
    vecs = embedder.encode([node_text(n) for n in nodes], batch_size=64)
    sim = vecs @ vecs.T
    n = len(nodes)

    th = _threshold(sim, n, min_sim, top_k, adaptive, target_density)

    # 度数封顶用的是 top_k 的宽松倍数。原因：`top_k` 限制的是"每节点向外发几条"，
    # 但边是无向去重的，一个节点会从多个邻居那里被动收到边，实际度数可以是
    # top_k 的好几倍。完全封死在 top_k 会把密度压得过低（实测 1.07 边/节点，
    # 低于健康区间）。所以卡在 2*top_k，既挡住超级枢纽，又不破坏整体密度。
    deg_cap = max(top_k, int(round(top_k * 2)))

    cand = {}
    for i in range(n):
        order = np.argsort(-sim[i])
        kept = 0
        for j in order:
            j = int(j)
            if j == i:
                continue
            if same_subject is not None and same_subject[j] != same_subject[i]:
                continue
            s = float(sim[i][j])
            if s < th or kept >= top_k:
                break
            cand[(min(i, j), max(i, j))] = round(s, 3)
            kept += 1

    cand = _cap_degree(cand, deg_cap)
    return [
        {"source": a, "target": b, "strength": s, "weight": 1}
        for (a, b), s in sorted(cand.items())
    ]


def _cap_degree(cand, max_deg):
    """按强度从高到低贪心保留边，直到所有节点度数都不超过 max_deg。"""
    deg = {}
    kept = {}
    for (a, b), s in sorted(cand.items(), key=lambda kv: -kv[1]):
        if deg.get(a, 0) >= max_deg or deg.get(b, 0) >= max_deg:
            continue
        kept[(a, b)] = s
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    return kept


def _threshold(sim, n, min_sim, top_k, adaptive, target_density):
    """决定本讲的实际余弦门槛。

    自适应思路：先用用户给的 min_sim 算一遍密度，如果明显偏离目标区间，
    就按需要的密度反推一个分位数门槛。这样既尊重用户配置，又能兜住
    "同一配置在不同讲次效果天差地别"的问题。
    """
    if not adaptive:
        return min_sim

    # 目标密度：默认取 top_k 的 70%，落在 1.4~2.2 健康区间
    if target_density is None:
        target_density = max(1.4, min(2.2, top_k * 0.7))

    off = sim[~np.eye(n, dtype=bool)]
    if off.size == 0:
        return min_sim

    # 固定门槛下的实际密度
    fixed_density = float((off >= min_sim).sum()) / n / 2.0
    if 0.6 * target_density <= fixed_density <= 1.8 * target_density:
        return min_sim  # 已经在合理范围，不用动

    # 反推：需要保留 top 多少比例的对
    keep_ratio = min(0.9, target_density / max(top_k, 1))
    # 每个节点保留 keep_ratio*top_k 个邻居 → 整体保留比例约 keep_ratio
    q = float(np.quantile(off, keep_ratio))
    return float(max(q, 0.35))  # 低于 0.35 的相似度基本没有语义意义


def build_cross_edges(nodes, embedder, cross_min_sim=0.82, cross_top_k=3):
    """跨科目关联边：只保留**不同科目**之间的高相似对。

    门槛比科内高得多（0.82 vs 0.60）——科内连错只是噪声，
    跨科连错会让人建立错误的类比，代价高。
    """
    if len(nodes) < 2:
        return []
    subjects = [n.get("subject", "") for n in nodes]
    if len(set(subjects)) < 2:
        return []
    vecs = embedder.encode([node_text(n) for n in nodes], batch_size=64)
    sim = vecs @ vecs.T
    n = len(nodes)

    cand = {}
    for i in range(n):
        order = np.argsort(-sim[i])
        kept = 0
        for j in order:
            j = int(j)
            if j == i or subjects[j] == subjects[i]:
                continue
            s = float(sim[i][j])
            if s < cross_min_sim or kept >= cross_top_k:
                break
            cand[(min(i, j), max(i, j))] = round(s, 3)
            kept += 1

    out = []
    for (a, b), s in sorted(cand.items()):
        out.append({
            "source": nodes[a].get("id", a),
            "target": nodes[b].get("id", b),
            "strength": s,
            "weight": 1,
            "cross": True,
        })
    return out
