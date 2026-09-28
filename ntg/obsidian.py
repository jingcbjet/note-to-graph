# -*- coding: utf-8 -*-
"""图谱 → Obsidian 笔记库（三层结构）。

为什么需要这一步：图谱是**索引层**，适合建立全局印象和发现意外关联，
但不适合逐条阅读——几百个节点散开、每条十几个字，读完记不住。
复习要的是能线性读、能双链跳、能靠反链回看的笔记，所以把同一份数据
再渲染一遍成 markdown。

三层：
  ① Notes/<科目>/第NN讲 X.md   —— 按讲分组的概念清单，复习主读物
  ② Concepts/<概念>.md         —— 白名单概念一文件一概念，双链枢纽
  ③ MOC/*.md                   —— 三科各自 MOC + 高频考点 + 总索引
"""
import io
import os
import re
from collections import defaultdict

from .config import safe_filename

# Windows/Unix 文件名禁用字符
BAD_CHARS = r'[\\/:*?"<>|#^\[\]]'

# 模板噪音：这些不是知识点，是讲义的脚手架，建词条只会污染库
NOISE_WORDS = ("本讲", "速记", "本章小结", "课堂小结", "知识部分", "题目部分")


def is_noise(label):
    return any(w in label for w in NOISE_WORDS)


class ObsidianBuilder:
    def __init__(self, graph, vault, subdir="知识图谱", log=print):
        self.graph = graph
        self.vault = os.path.join(vault, subdir) if subdir else vault
        self.log = log

        self.nodes = graph["nodes"]
        self.cross = graph.get("crossEdges", [])
        self.idx = {n["id"]: n for n in self.nodes}
        self.has_subjects = bool(graph.get("subjects"))

        # 标签 → 节点（同一概念可能出现在多讲/多科）
        self.label_nodes = defaultdict(list)
        for n in self.nodes:
            self.label_nodes[n["label"]].append(n)

        # 讲次 → 节点
        self.lec_nodes = defaultdict(list)
        for n in self.nodes:
            self.lec_nodes[n["lecture"]].append(n)

        # 关联索引
        self.cross_map = defaultdict(list)
        for e in self.cross:
            if e["source"] in self.idx and e["target"] in self.idx:
                self.cross_map[e["source"]].append((self.idx[e["target"]], e["strength"]))
                self.cross_map[e["target"]].append((self.idx[e["source"]], e["strength"]))

        self.local_map = defaultdict(list)
        for e in graph["edges"]:
            if e["source"] in self.idx and e["target"] in self.idx:
                self.local_map[e["source"]].append(self.idx[e["target"]])
                self.local_map[e["target"]].append(self.idx[e["source"]])

        self.plan = {"notes": [], "concepts": [], "moc": []}
        self.concept_ok = self._pick_concepts()

    # ------------------------------------------------------------ 白名单
    def _pick_concepts(self):
        """决定哪些概念值得单独建词条。

        全量概念都建文件会把库淹没（几千个文件、大量空链接）。
        入选规则（任一满足）：
          a. 跨科/跨讲出现 → 天然是枢纽概念
          b. importance=5 且科内度数 >=3 且标签 >=4 字 → 考点中心
        """
        ok = set()
        for label, ns in self.label_nodes.items():
            if is_noise(label):
                continue
            if len({x.get("subject") for x in ns}) > 1:
                ok.add(label)
                continue
            imp5 = any(int(x.get("importance") or 0) == 5 for x in ns)
            deg = sum(len(self.local_map[x["id"]]) for x in ns)
            if imp5 and deg >= 3 and len(label) >= 4:
                ok.add(label)
        return ok

    def ref(self, label):
        """已建词条的用双链，否则纯文本 —— 避免产生一堆灰色空链接。"""
        return "[[%s]]" % safe_filename(label) if label in self.concept_ok else label

    def _sub(self, n):
        return n.get("subjectName") or ""

    def _title_of(self, lec):
        ns = self.lec_nodes[lec]
        return (ns[0].get("lectureTitle") or ("第%02d讲" % (lec % 1000))), ns[0]

    def _local_no(self, lec):
        return lec % 1000 if self.has_subjects else lec

    # ------------------------------------------------------------ ① 讲次笔记
    def build_notes(self):
        for lec in sorted(self.lec_nodes):
            ns = self.lec_nodes[lec]
            title, first = self._title_of(lec)
            sub_name = self._sub(first)
            local_no = self._local_no(lec)

            L = ["---"]
            if sub_name:
                L.append("科目: %s" % sub_name)
            L.append("讲次: %d" % local_no)
            L.append("标签:\n  - 知识图谱")
            L.append("概念数: %d" % len(ns))
            L.append("---\n")
            L.append("# %s\n" % title)
            L.append("> [!info] 本讲速览")
            L.append("> 共 %d 个概念 · [[%s MOC|返回索引]]"
                     % (len(ns), sub_name) if sub_name else "> 共 %d 个概念" % len(ns))
            L.append("")

            for n in sorted(ns, key=lambda x: (-int(x.get("importance") or 0), x["label"])):
                imp = int(n.get("importance") or 0)
                star = " ⭐" if imp == 5 else (" •" if imp == 4 else "")
                L.append("### %s%s" % (n["label"], star))
                L.append("")
                L.append(n["content"])
                rel = self._relations(n)
                if rel:
                    L.append("")
                    L.append("关联：%s" % " · ".join(rel))
                L.append("")

            path = os.path.join("Notes", sub_name or "", "%s.md" % safe_filename(title))
            self.plan["notes"].append({"path": path, "content": "\n".join(L), "lecture": lec})

    def _relations(self, n):
        out = []
        for nb in self.local_map[n["id"]][:4]:
            if nb["id"] != n["id"]:
                out.append(self.ref(nb["label"]))
        for nb, s in self.cross_map[n["id"]][:2]:
            if nb["id"] != n["id"]:
                tag = " <small>(%s)</small>" % self._sub(nb) if self._sub(nb) else ""
                out.append("%s%s" % (self.ref(nb["label"]), tag))
        return list(dict.fromkeys(out))

    # ------------------------------------------------------------ ② 概念词条
    def build_concepts(self):
        for label, ns in self.label_nodes.items():
            if label not in self.concept_ok:
                continue
            n = ns[0]
            L = ["---", "概念: %s" % label]
            L.append("重要度: %d" % int(n.get("importance") or 0))
            L.append("标签:\n  - 概念")
            L.append("---\n")
            L.append("# %s\n" % label)
            L.append("> [!note] 定义")
            for line in n["content"].split("\n"):
                L.append("> " + line)
            L.append("")

            L.append("## 出现位置\n")
            for x in ns:
                lt, _ = self._title_of(x["lecture"])
                L.append("- %s · 第%02d讲 [[%s]]" % (
                    self._sub(x) or "本课程", self._local_no(x["lecture"]),
                    safe_filename(lt)))
            L.append("")

            # 相关概念（科内邻居）
            nb_all = []
            for x in ns:
                nb_all.extend(self.local_map[x["id"]])
            items, seen = [], set()
            for nb in nb_all:
                if nb["label"] in seen or nb["label"] == label:
                    continue
                seen.add(nb["label"])
                items.append(nb)
                if len(items) >= 8:
                    break
            if items:
                L.append("## 相关概念\n")
                for nb in items:
                    L.append("- %s <small>%s</small>"
                             % (self.ref(nb["label"]), self._sub(nb) or ""))
                L.append("")

            # 跨科关联
            xb = []
            for x in ns:
                xb.extend(self.cross_map[x["id"]])
            if xb:
                L.append("## 跨科关联 🔗\n")
                seen = set()
                for nb, s in sorted(xb, key=lambda t: -t[1])[:6]:
                    if nb["label"] in seen or nb["label"] == label:
                        continue
                    seen.add(nb["label"])
                    L.append("- %s · %s <small>相似度 %.2f</small>"
                             % (self.ref(nb["label"]), self._sub(nb) or "", s))
                L.append("")

            self.plan["concepts"].append({
                "path": os.path.join("Concepts", "%s.md" % safe_filename(label)),
                "content": "\n".join(L),
            })

    # ------------------------------------------------------------ ③ MOC
    def build_moc(self):
        subjects = self.graph.get("subjects")
        if subjects:
            for s in subjects:
                lecs = sorted(s["lectures"])
                L = ["---", "类型: MOC", "科目: %s" % s["name"], "---\n"]
                L.append("# %s · 索引\n" % s["name"])
                L.append("共 %d 讲 · %d 个概念\n" % (len(lecs), s["nodeCount"]))
                L.append("## 讲次列表\n")
                for lec in lecs:
                    title, _ = self._title_of(lec)
                    L.append("- 第%02d讲 [[%s]] <small>%d 概念</small>"
                             % (self._local_no(lec), safe_filename(title),
                                len(self.lec_nodes[lec])))
                L.append("")
                self.plan["moc"].append(
                    {"path": os.path.join("MOC", "%s MOC.md" % s["name"]),
                     "content": "\n".join(L)})

        # 高频考点：只收 ⭐，考前突击入口
        hl = defaultdict(list)
        for label, ns in self.label_nodes.items():
            if is_noise(label):
                continue
            if any(int(x.get("importance") or 0) == 5 for x in ns):
                hl[ns[0].get("subjectName") or "全部"].append(label)
        L = ["---", "类型: MOC", "标签:\n  - 考点", "---\n"]
        L.append("# 高频考点\n")
        total = sum(len(v) for v in hl.values())
        L.append("共 %d 个核心考点（重要度 5）。考前过一遍这里。\n" % total)
        for k in sorted(hl):
            L.append("## %s（%d 个）\n" % (k, len(hl[k])))
            for lb in sorted(set(hl[k])):
                L.append("- %s" % self.ref(lb))
            L.append("")
        self.plan["moc"].append({"path": os.path.join("MOC", "高频考点 MOC.md"),
                                 "content": "\n".join(L)})

        # 总索引
        n_nodes = len(self.nodes)
        n_lec = len(self.lec_nodes)
        L = ["---", "类型: MOC", "标签:\n  - 知识图谱", "---\n"]
        L.append("# 知识图谱 · 总索引\n")
        if subjects:
            L.append("| 科目 | 讲次 | 节点 | 关联边 |")
            L.append("|---|---|---|---|")
            for s in subjects:
                # 表格里的别名管道不要转义：[[X\|Y]] 会渲染出字面反斜杠
                L.append("| [[%s MOC|%s]] | %d | %d | %d |"
                         % (s["name"], s["name"], len(s["lectures"]),
                            s["nodeCount"], s["edgeCount"]))
            L.append("")
        L.append("> [!tip] 使用方式")
        L.append("> 1. **按讲复习** → 进各科目 MOC 挑讲次")
        L.append("> 2. **按概念复习** → 打开概念词条，用反链面板看它出现在哪些讲")
        L.append("> 3. **找跨科联系** → 概念词条的「跨科关联」段落（共 %d 条）"
                 % len(self.cross))
        L.append("> 4. **背考点** → 笔记里 ⭐ 是重要度 5 的核心考点")
        L.append("> 5. **考前突击** → [[高频考点 MOC]]")
        L.append("")
        L.append("## 统计\n")
        L.append("| 项目 | 数量 |")
        L.append("|---|---|")
        L.append("| 讲次笔记 | %d |" % n_lec)
        L.append("| 概念词条 | %d |" % len(self.concept_ok))
        L.append("| 全部概念（去重） | %d |" % len(self.label_nodes))
        L.append("| 图谱节点 | %d |" % n_nodes)
        L.append("")
        self.plan["moc"].append({"path": "总索引.md", "content": "\n".join(L)})

    # ------------------------------------------------------------ 写盘
    def build(self, clean=True):
        self.concept_ok = self._pick_concepts()
        self.build_notes()
        self.build_concepts()
        self.build_moc()

        if clean and os.path.isdir(self.vault):
            import shutil
            shutil.rmtree(self.vault)

        written = 0
        for p in self.plan["notes"] + self.plan["concepts"] + self.plan["moc"]:
            fp = os.path.join(self.vault, p["path"])
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            io.open(fp, "w", encoding="utf-8").write(p["content"])
            written += 1

        stats = {
            "notes": len(self.plan["notes"]),
            "concepts": len(self.plan["concepts"]),
            "moc": len(self.plan["moc"]),
            "files": written,
            "vault": self.vault,
        }
        self.log("Obsidian: %d 文件 (讲次 %d / 概念 %d / MOC %d) -> %s"
                 % (written, stats["notes"], stats["concepts"], stats["moc"], self.vault))
        return stats

    # ------------------------------------------------------------ 校验
    def check_links(self):
        """断链自检：双链目标必须真的存在，否则 Obsidian 里一片灰色。"""
        files = set()
        for p in self.plan["notes"] + self.plan["concepts"] + self.plan["moc"]:
            files.add(os.path.splitext(os.path.basename(p["path"]))[0])
        total, broken, selfn = 0, [], 0
        for p in self.plan["notes"] + self.plan["concepts"] + self.plan["moc"]:
            stem = os.path.splitext(os.path.basename(p["path"]))[0]
            for m in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", p["content"]):
                m = m.strip()
                total += 1
                if m == stem:
                    selfn += 1
                if m not in files:
                    broken.append(m)
        return {"links": total, "broken": broken, "self": selfn}
