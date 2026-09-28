# -*- coding: utf-8 -*-
"""图谱数据构建：把各讲节点合成一张图，并渲染成单文件 HTML。

合集模式把多科目合到一张图里，做法是：
  ① 节点 id 全局顺延（避免不同科目 id 撞车）
  ② lecture 改全局唯一编号 = 科目序号 * 1000 + 原讲号
  ③ 边同步 remap
这样才能在同一个 ECharts 实例里按科目切 tab 而不串数据。
"""
import io
import json
import os


def package_lectures(lectures_nodes, subject_key=None, subject_name=None,
                     subtitle="", teacher=""):
    """把若干讲的节点合成单科目图。

    lectures_nodes: [(num, title, [node...]), ...]
    返回 (graph_dict, meta)
    """
    all_nodes, all_edges = [], []
    lec_count = {}
    offset = 0
    for num, title, nodes in lectures_nodes:
        local_edges = nodes.pop("__edges__", []) if isinstance(nodes, dict) else []
        for i, n in enumerate(nodes):
            all_nodes.append({
                "id": offset + i,
                "lecture": num,
                "lectureTitle": title,
                "label": n["label"],
                "content": n["content"],
                "importance": int(n.get("importance", 3)),
                "weight": len(n["content"]),
            })
        for e in local_edges:
            all_edges.append({
                "source": offset + e["source"],
                "target": offset + e["target"],
                "strength": e["strength"],
                "weight": 1,
            })
        lec_count[num] = len(nodes)
        offset += len(nodes)

    lectures_meta = [{"num": n, "title": t, "nodeCount": lec_count.get(n, 0)}
                     for n, t, _ in lectures_nodes]
    graph = {"nodes": all_nodes, "edges": all_edges, "lectures": lectures_meta}
    meta = {
        "subject": subject_key, "subjectName": subject_name,
        "subtitle": subtitle, "teacher": teacher,
    }
    return graph, meta


def merge_subjects(subject_graphs, colors=None, key_order=None):
    """多科目合并成合集图。subject_graphs: [(key, name, subtitle, graph), ...]"""
    colors = colors or {}
    default_colors = ["#3b5bdb", "#0f8a5f", "#c2571a", "#8e44ad", "#c0392b"]
    if key_order is None:
        key_order = [s[0] for s in subject_graphs]

    all_nodes, all_edges, subjects_meta = [], [], []
    offset = 0
    for si, (key, name, subtitle, g) in enumerate(subject_graphs):
        local_edges = g.get("edges", [])
        for n in g["nodes"]:
            n["lecture"] = si * 1000 + n["lecture"]
            n["id"] += offset
            n["subject"] = key
            n["subjectName"] = name
            n["teacher"] = g.get("teacher", "")
        for e in local_edges:
            e["source"] += offset
            e["target"] += offset
        all_nodes.extend(g["nodes"])
        all_edges.extend(local_edges)
        offset = max((n["id"] for n in all_nodes), default=-1) + 1

        lec_nums = sorted({n["lecture"] for n in g["nodes"]})
        subjects_meta.append({
            "key": key, "name": name,
            "subtitle": subtitle or "知识图谱",
            "color": colors.get(key) or default_colors[si % len(default_colors)],
            "lectures": lec_nums,
            "nodeCount": len(g["nodes"]),
            "edgeCount": len(local_edges),
        })
    return {
        "nodes": all_nodes, "edges": all_edges,
        "subjects": subjects_meta,
    }


def attach_cross_edges(graph, cross_edges, cross_min_sim=0.82, cross_top_k=3):
    """把跨科边挂到合集图上（列表里的 source/target 已是全局 id）。"""
    graph = dict(graph)
    graph["crossEdges"] = cross_edges
    graph.setdefault("meta", {})
    graph["meta"]["crossMinSim"] = cross_min_sim
    graph["meta"]["crossTopK"] = cross_top_k
    return graph


TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template_graph.html")


def render_html(graph, out_path, title="知识图谱", subtitle="", model="", template=None):
    """把 graph 注入模板，产出单文件 HTML。"""
    tpl = io.open(template or TEMPLATE, encoding="utf-8").read()

    if graph.get("subjects"):
        lec_total = max((max(s["lectures"]) for s in graph["subjects"] if s["lectures"]),
                        default=0)
    else:
        lec_total = max((n["lecture"] for n in graph["nodes"]), default=0)

    payload = json.dumps(graph, ensure_ascii=False, separators=(",", ":"))
    html = (tpl
            .replace("__TITLE__", _esc(title))
            .replace("__SUBTITLE__", _esc(subtitle))
            .replace("__MODEL__", _esc(model))
            .replace("__LECTURE_COUNT__", str(lec_total))
            .replace("__GRAPH_DATA__", payload))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    io.open(out_path, "w", encoding="utf-8").write(html)
    return out_path


def _esc(s):
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def save_json(obj, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return path


def load_json(path):
    return json.load(io.open(path, encoding="utf-8"))
