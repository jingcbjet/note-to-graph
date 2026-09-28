# -*- coding: utf-8 -*-
"""主流程：输入 → 节点抽取 → 算边 → 图谱 HTML →（可选）Obsidian 笔记库。

每讲的节点结果都增量落盘（cache/ 下），重跑时命中缓存直接跳过 —— 这是
调试和补跑的关键：改下游渲染逻辑不该重花 LLM 的钱，也绝不该重跑几十分钟。
"""
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config as cfgmod
from . import embed as embedmod
from . import graph as graphmod
from . import llm as llmmod
from . import loaders
from . import splitter


class Pipeline:
    def __init__(self, cfg, api_key=None, log=print):
        self.cfg = cfg
        self.log = log
        self.api_key = api_key
        self.workdir = cfg["output"]["workdir"]
        self.cache_dir = os.path.join(self.workdir, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self._embedder = None

    # ------------------------------------------------------------ 资源
    @property
    def embedder(self):
        if self._embedder is None:
            e = self.cfg["embed"]
            self._embedder = embedmod.Embedder(e["model"], e["device"])
        return self._embedder

    def _cache_path(self, tag, num):
        return os.path.join(self.cache_dir, "nodes_%s_L%02d.json" % (tag, num))

    # ------------------------------------------------------------ 抽取
    def extract_one(self, lec, tag, domain, course):
        """抽一讲的节点，带缓存。返回 (nodes, seconds, from_cache)。"""
        cp = self._cache_path(tag, lec["num"])
        if os.path.exists(cp):
            try:
                d = json.load(io.open(cp, encoding="utf-8"))
                if d.get("nodes"):
                    return d["nodes"], 0.0, True
            except (ValueError, KeyError):
                pass  # 缓存坏了就当没有

        # 切分（本地，免费）——每块单独送也许更省 token，但一次送全文
        # 能让模型看到全局，概念覆盖更均匀，实测质量更好
        blocks = splitter.split_blocks(
            lec["text"],
            min_merge=self.cfg["split"]["min_merge"],
            max_cap=self.cfg["split"]["max_cap"],
        )
        self.log("  切分 %d 块" % len(blocks))

        nodes, usage, dt = llmmod.extract_with_retry(
            lec["title"], lec["text"], self.cfg, self.api_key,
            domain=domain, course=course, log=self.log,
        )
        json.dump({"nodes": nodes, "usage": usage, "seconds": round(dt, 1)},
                  io.open(cp, "w", encoding="utf-8"), ensure_ascii=False)
        return nodes, dt, False

    def run_extract(self, lectures, tag, domain="本课程", course="课程笔记"):
        """并发抽全部讲次的节点。"""
        workers = max(1, int(self.cfg["llm"].get("concurrency", 4)))
        out, fail = {}, []
        t0 = time.time()
        done = [0]

        def task(lec):
            try:
                nodes, dt, cached = self.extract_one(lec, tag, domain, course)
                return lec, nodes, dt, cached, None
            except Exception as e:  # noqa: BLE001
                return lec, None, 0.0, False, e

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(task, lec) for lec in lectures]
            for f in as_completed(futs):
                lec, nodes, dt, cached, err = f.result()
                done[0] += 1
                if err or not nodes:
                    fail.append(lec["num"])
                    self.log("[%d/%d] 第%02d讲 失败: %s"
                             % (done[0], len(lectures), lec["num"], err))
                else:
                    out[lec["num"]] = nodes
                    flag = "缓存" if cached else "%.1fs" % dt
                    self.log("[%d/%d] 第%02d讲: %d 节点 (%s)"
                             % (done[0], len(lectures), lec["num"], len(nodes), flag))
        self.log("抽取完成: 成功 %d / 失败 %d, 墙钟 %.1fs"
                 % (len(out), len(fail), time.time() - t0))
        return out, fail

    # ------------------------------------------------------------ 算边
    def build_means(self, lectures, nodes_by_num, tag):
        """算科内边，产出 [(num, title, [node...], [edge...])]。"""
        g = self.cfg["graph"]
        td = g.get("target_density") or None
        results = []
        for lec in lectures:
            nodes = nodes_by_num.get(lec["num"])
            if not nodes:
                continue
            edges = embedmod.build_edges(
                nodes, self.embedder,
                top_k=g["top_k"], min_sim=g["min_sim"],
                adaptive=g.get("adaptive_sim", True),
                target_density=td,
            )
            results.append((lec["num"], lec["title"], list(nodes), edges))
        return results

    # ------------------------------------------------------------ 顶层
    def build(self, source, subject_key="subject", subject_name="课程",
              subtitle="", domain="本课程", tag=None, title=None,
              make_html=True):
        """单科目完整流程。返回 (graph, out_html_path)。"""
        tag = tag or subject_key
        lectures = loaders.load_lectures(source)
        if not lectures:
            raise RuntimeError("没有从 %s 读到任何讲次，请检查路径与文件命名" % source)
        self.log("读到 %d 讲 (来源: %s)" % (len(lectures), source))

        nodes_by_num, fail = self.run_extract(
            lectures, tag, domain=domain, course=subject_name)

        self.log("本地算边中…")
        t0 = time.time()
        means = self.build_means(lectures, nodes_by_num, tag)
        self.log("算边完成 %.1fs" % (time.time() - t0))

        all_nodes, all_edges = [], []
        offset = 0
        for num, ltitle, nodes, edges in means:
            for i, n in enumerate(nodes):
                all_nodes.append({
                    "id": offset + i, "lecture": num, "lectureTitle": ltitle,
                    "label": n["label"], "content": n["content"],
                    "importance": int(n.get("importance", 3)),
                    "weight": len(n["content"]),
                })
            for e in edges:
                all_edges.append({"source": offset + e["source"],
                                  "target": offset + e["target"],
                                  "strength": e["strength"], "weight": 1})
            offset += len(nodes)

        graph = {
            "nodes": all_nodes, "edges": all_edges,
            "lectures": [{"num": n, "title": t, "nodeCount": len(nd)}
                         for n, t, nd, _ in means],
            "meta": {"subject": subject_key, "subjectName": subject_name,
                     "subtitle": subtitle, "failedLectures": fail},
        }
        graphmod.save_json(graph, os.path.join(self.workdir, "graph_data_%s.json" % tag))
        self.log("总计 %d 节点 / %d 边 (%.2f 边/节点)"
                 % (len(all_nodes), len(all_edges),
                    len(all_edges) / max(1, len(all_nodes))))

        out_html = None
        if make_html:
            out_html = os.path.join(
                self.workdir,
                "%s.html" % (subject_key if subject_key != "subject" else "graph"))
            graphmod.render_html(
                graph, out_html,
                title=title or subject_name,
                subtitle=subtitle,
                model=self.cfg["llm"]["model"],
            )
            self.log("HTML -> %s (%d KB)" % (out_html, os.path.getsize(out_html) // 1024))
        return graph, out_html

    def add_cross_edges(self, collection_graph, out_path=None):
        """给合集图加跨科边。"""
        g = self.cfg["graph"]
        self.log("计算跨科关联…")
        t0 = time.time()
        cross = embedmod.build_cross_edges(
            collection_graph["nodes"], self.embedder,
            cross_min_sim=g["cross_min_sim"], cross_top_k=g["cross_top_k"],
        )
        collection_graph = graphmod.attach_cross_edges(
            collection_graph, cross,
            cross_min_sim=g["cross_min_sim"], cross_top_k=g["cross_top_k"])
        self.log("跨科边 %d 条, %.1fs" % (len(cross), time.time() - t0))
        if out_path:
            graphmod.save_json(collection_graph, out_path)
        return collection_graph, cross
