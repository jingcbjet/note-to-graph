# -*- coding: utf-8 -*-
"""离线自检：不打 LLM、不下载模型，只验证数据流与渲染正确性。

跑法：python tests/test_offline.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ntg import config, graph as graphmod, loaders, splitter  # noqa: E402
from ntg.embed import build_edges  # noqa: E402
from ntg.obsidian import ObsidianBuilder  # noqa: E402

# 本文件全程 print 中文。Windows 英文系统默认 cp1252 编码 stdout，
# 不切 UTF-8 会 UnicodeEncodeError（CI windows-latest 就踩过）。
config.force_utf8_stdio()

PASS, FAIL = [], []


class FakeEmbedder:
    """离线假的 embedder，用可控的相似度结构验证算边逻辑。

    构造一个"两簇"结构：前一半概念彼此高相似，后一半彼此高相似，
    跨簇低相似。用来验证 top_k 剪枝和自适应门槛的行为。
    """

    def __init__(self, dim=8, seed=0):
        import numpy as np
        rng = np.random.default_rng(seed)
        self.dim = dim
        self.rng = rng
        self.n = 0

    def encode(self, texts, batch_size=64):
        import numpy as np
        n = len(texts)
        self.n = n
        half = n // 2
        # 簇心
        c1 = np.zeros(self.dim); c1[0] = 1.0
        c2 = np.zeros(self.dim); c2[1] = 1.0
        out = []
        for i in range(n):
            base = c1 if i < half else c2
            # 加噪声控制簇内相似度
            v = base + self.rng.normal(0, 0.35, self.dim)
            v = v / (np.linalg.norm(v) + 1e-9)
            out.append(v)
        return np.array(out)



def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name,
                         (" — " + extra) if extra else ""))


def make_graph():
    """造两张图的合集：两个科目，各两讲，含跨科边。"""
    def node(i, lec, label, imp, sub, subname):
        return {"id": i, "lecture": lec, "lectureTitle": "第%02d讲 测试" % (lec % 1000),
                "label": label, "content": "这是 %s 的说明内容。" % label,
                "importance": imp, "weight": 10, "subject": sub, "subjectName": subname}

    g1 = {
        "nodes": [
            node(0, 1, "社会支持理论", 5, "a", "科目甲"),
            node(1, 1, "生态系统理论", 4, "a", "科目甲"),
            node(2, 2, "个案管理特点", 5, "a", "科目甲"),
            node(3, 2, "结案负面反应处理总结", 3, "a", "科目甲"),
        ],
        "edges": [{"source": 0, "target": 1, "strength": 0.8, "weight": 1},
                  {"source": 2, "target": 3, "strength": 0.7, "weight": 1}],
    }
    g2 = {
        "nodes": [
            node(0, 1, "非理性信念辩论技巧", 5, "b", "科目乙"),
            node(1, 1, "社会支持理论", 5, "b", "科目乙"),
            node(2, 2, "理性情绪治疗ABC理论", 4, "b", "科目乙"),
        ],
        "edges": [{"source": 0, "target": 2, "strength": 0.75, "weight": 1},
                  {"source": 1, "target": 2, "strength": 0.65, "weight": 1}],
    }
    return graphmod.merge_subjects(
        [("a", "科目甲", "甲老师", g1), ("b", "科目乙", "乙老师", g2)],
        colors={"a": "#3b5bdb", "b": "#0f8a5f"},
    )


def main():
    tmp = tempfile.mkdtemp(prefix="ntg_test_")
    try:
        print("\n[1] 配置层")
        cfg = config.load_config()
        check("默认配置可加载", cfg["graph"]["top_k"] == 3)
        check("force_utf8_stdio 可调用", callable(config.force_utf8_stdio))
        cfg2 = config.load_config(overrides={"graph": {"top_k": 9}})
        check("overrides 生效", cfg2["graph"]["top_k"] == 9)
        check("嵌套默认值未被覆盖", cfg2["graph"]["min_sim"] == 0.60)
        check("文件名清洗", config.safe_filename('a/b:c*d') == "abcd")

        print("\n[2] 切分")
        md = "# 标题一\n" + ("内容" * 20) + "\n\n## 标题二\n短\n\n## 标题三\n" + ("字" * 1000)
        blocks = splitter.split_blocks(md, min_merge=120, max_cap=900)
        check("切出多块", len(blocks) >= 2, "%d 块" % len(blocks))
        check("超大块被二次切分", all(len(b) <= 1200 for _, b in blocks))

        print("\n[3] HTML→markdown")
        h = "<h2>小标题</h2><p>正文<strong>加粗</strong></p><ul><li>项一</li><li>项二</li></ul>"
        m = loaders.html_to_md(h)
        check("h2 转 ##", "## 小标题" in m)
        check("li 转 -", "- 项一" in m)
        check("strong 转 **", "**加粗**" in m)

        print("\n[4] 多科目合并")
        g = make_graph()
        check("节点数", len(g["nodes"]) == 7, str(len(g["nodes"])))
        check("科目数", len(g["subjects"]) == 2)
        ids = [n["id"] for n in g["nodes"]]
        check("id 唯一", len(ids) == len(set(ids)))
        # lecture 应全局化：科目乙变成 1001 / 1002
        lecs = sorted({n["lecture"] for n in g["nodes"]})
        check("lecture 全局编号", lecs == [1, 2, 1001, 1002], str(lecs))
        idset = set(ids)
        dang = [e for e in g["edges"]
                if e["source"] not in idset or e["target"] not in idset]
        check("无悬空边", not dang, str(dang[:2]))

        print("\n[6] 算边与自适应门槛")
        nodes30 = [{"label": "概念%02d" % i, "content": "说明文本 %d" % i} for i in range(30)]
        emb = FakeEmbedder(seed=7)

        e = build_edges(nodes30, emb, top_k=3, min_sim=0.0, adaptive=False)
        # top_k 限制的是"每节点向外发几条"；边是无向去重的，所以实际度数
        # 上界是 2*top_k（内部按此封顶），能挡住超级枢纽。
        from collections import Counter
        deg = Counter()
        for x in e:
            deg[x["source"]] += 1
            deg[x["target"]] += 1
        check("每节点度数不超过 2*top_k", max(deg.values()) <= 6,
              "最大度数 %d" % max(deg.values()))
        check("存在边", len(e) > 0)
        ids = set(range(30))
        check("边两端合法",
              all(x["source"] in ids and x["target"] in ids for x in e))
        check("无自环", all(x["source"] != x["target"] for x in e))

        # 自适应：极高门槛在自适应下也应产出接近目标密度的边
        e_hi = build_edges(nodes30, emb, top_k=3, min_sim=0.99, adaptive=True)
        e_fixed = build_edges(nodes30, emb, top_k=3, min_sim=0.99, adaptive=False)
        check("固定高门槛几乎无边", len(e_fixed) == 0, "%d 条" % len(e_fixed))
        check("自适应能兜住高门槛", len(e_hi) > 10,
              "%d 条" % len(e_hi))
        check("自适应密度落在健康区间",
              1.0 <= len(e_hi) / 30 <= 3.0,
              "%.2f" % (len(e_hi) / 30))

        print("\n[7] HTML 渲染")
        out_html = os.path.join(tmp, "g.html")
        graphmod.render_html(g, out_html, title="测试图谱", subtitle="副标题", model="test-model")
        html = open(out_html, encoding="utf-8").read()
        check("文件已生成", os.path.getsize(out_html) > 1000)
        check("无未替换占位符",
              "__GRAPH_DATA__" not in html and "__TITLE__" not in html
              and "__LECTURE_COUNT__" not in html)
        check("标题已注入", "测试图谱" in html)
        check("数据已注入", "社会支持理论" in html)

        print("\n[6] Obsidian 生成")
        vault = os.path.join(tmp, "vault")
        b = ObsidianBuilder(g, vault, subdir="测试图谱")
        stats = b.build()
        chk = b.check_links()
        check("讲次笔记数", stats["notes"] == 4, str(stats["notes"]))
        check("生成了概念词条", stats["concepts"] >= 1, str(stats["concepts"]))
        check("MOC 数 >=3", stats["moc"] >= 3, str(stats["moc"]))
        check("断链为 0", not chk["broken"], str(chk["broken"][:3]))
        check("自指为 0", chk["self"] == 0, str(chk["self"]))
        check("模板噪音被剔除", "结案负面反应处理总结" not in b.concept_ok)
        # 跨科出现的概念必须建词条（白名单规则 a）
        check("跨科概念入选", "社会支持理论" in b.concept_ok)
        check("总索引存在", os.path.exists(os.path.join(b.vault, "总索引.md")))
        check("高频考点 MOC 存在",
              os.path.exists(os.path.join(b.vault, "MOC", "高频考点 MOC.md")))

        print("\n" + "=" * 46)
        print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
        if FAIL:
            print("失败项: %s" % ", ".join(FAIL))
        return 1 if FAIL else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
