# -*- coding: utf-8 -*-
"""note-to-graph 命令行入口。

    ntg build   <输入>          # 单科目：抽取 → 算边 → 图谱 HTML
    ntg collect <配置>          # 多科目合集 + 跨科关联
    ntg obsidian <graph.json>   # 图谱 → Obsidian 笔记库
    ntg check   <graph.json>    # 数据体检
"""
import argparse
import io
import json
import os
import sys

from . import config as cfgmod
from . import graph as graphmod
from .obsidian import ObsidianBuilder
from .pipeline import Pipeline


def _cfg(args):
    cfg = cfgmod.load_config(args.config, overrides=None)
    cfg = cfgmod.apply_provider(cfg)
    if args.workdir:
        cfg["output"]["workdir"] = args.workdir
    if args.device:
        cfg["embed"]["device"] = args.device
    cfgmod.setup_env(cfg)
    return cfg


def _key(cfg, args):
    return cfgmod.resolve_api_key(cfg, getattr(args, "api_key", None))


# ---------------------------------------------------------------- build
def cmd_build(args):
    cfg = _cfg(args)
    if args.workdir:
        os.makedirs(args.workdir, exist_ok=True)
    p = Pipeline(cfg, api_key=_key(cfg, args))
    graph, html = p.build(
        args.source,
        subject_key=args.tag or "subject",
        subject_name=args.name or "课程",
        subtitle=args.subtitle or "",
        domain=args.domain or "本课程",
        tag=args.tag,
        title=args.title,
    )
    if cfg["output"].get("obsidian_vault"):
        _write_obsidian(cfg, graph, args)
    print("\n完成。图谱 HTML: %s" % html)
    return 0


# ---------------------------------------------------------------- collect
def cmd_collect(args):
    """多科目合集。

    清单文件（YAML 或 JSON）：
        subjects:
          - key: law
            name: 法规与政策
            subtitle: 杨老师精讲
            source: ./notes/law
            color: "#3b5bdb"
          - key: prac
            name: 社会工作实务
            source: ./notes/practice
    """
    cfg = _cfg(args)
    entries = _load_subject_list(args.manifest)
    p = Pipeline(cfg, api_key=_key(cfg, args))

    subject_graphs = []
    for e in entries:
        key = e["key"]
        name = e.get("name") or key
        p.log("\n=== %s ===" % name)
        g, _ = p.build(
            e["source"],
            subject_key=key, subject_name=name,
            subtitle=e.get("subtitle", ""), domain=e.get("domain", "本课程"),
            tag=key, make_html=False,
        )
        subject_graphs.append((key, name, e.get("subtitle", ""), g))

    colors = {e["key"]: e["color"] for e in entries if e.get("color")}
    coll = graphmod.merge_subjects(subject_graphs, colors=colors)
    coll, cross = p.add_cross_edges(coll)

    json_path = os.path.join(cfg["output"]["workdir"], "graph_collection.json")
    graphmod.save_json(coll, json_path)

    html_path = os.path.join(cfg["output"]["workdir"], "collection.html")
    graphmod.render_html(
        coll, html_path,
        title=args.title or "知识图谱合集",
        subtitle=" · ".join(s[1] for s in subject_graphs),
        model=cfg["llm"]["model"],
    )
    print("\n合集: %d 节点 / %d 边 / %d 跨科边" %
          (len(coll["nodes"]), len(coll["edges"]), len(cross)))
    print("JSON: %s\nHTML: %s" % (json_path, html_path))

    if cfg["output"].get("obsidian_vault"):
        _write_obsidian(cfg, coll, args)
    return 0


def _load_subject_list(path):
    raw = io.open(path, encoding="utf-8").read()
    if path.endswith((".yaml", ".yml")):
        import yaml
        data = yaml.safe_load(raw)
    else:
        data = json.loads(raw)
    subs = data.get("subjects") if isinstance(data, dict) else data
    if not subs:
        raise RuntimeError("清单里没有 subjects")
    for i, s in enumerate(subs):
        s.setdefault("key", "s%d" % i)
    return subs


# ---------------------------------------------------------------- obsidian
def cmd_obsidian(args):
    cfg = _cfg(args)
    g = graphmod.load_json(args.graph)
    vault = args.vault or cfg["output"].get("obsidian_vault")
    if not vault:
        print("请用 --vault 指定 Obsidian 库路径（或在配置里设 output.obsidian_vault）")
        return 2
    b = ObsidianBuilder(g, vault, subdir=args.subdir or "知识图谱")
    stats = b.build(clean=not args.no_clean)
    chk = b.check_links()
    print("\n双链 %d / 断链 %d / 自指 %d" %
          (chk["links"], len(chk["broken"]), chk["self"]))
    for m in chk["broken"][:10]:
        print("  断链: %s" % m)
    return 0 if not chk["broken"] else 1


def _write_obsidian(cfg, graph, args):
    vault = cfg["output"]["obsidian_vault"]
    b = ObsidianBuilder(graph, vault, subdir=cfg["output"].get("obsidian_subdir", "知识图谱"))
    b.build()
    chk = b.check_links()
    print("Obsidian 双链 %d / 断链 %d" % (chk["links"], len(chk["broken"])))


# ---------------------------------------------------------------- check
def cmd_check(args):
    g = graphmod.load_json(args.graph)
    nodes, edges = g["nodes"], g["edges"]
    ids = [n["id"] for n in nodes]
    dup = len(ids) - len(set(ids))
    idset = set(ids)
    dangling = [e for e in edges
                if e["source"] not in idset or e["target"] not in idset]
    density = len(edges) / max(1, len(nodes))
    print("节点 %d / 边 %d" % (len(nodes), len(edges)))
    print("id 重复: %d" % dup)
    print("悬空边: %d" % len(dangling))
    print("边密度: %.2f 边/节点  (健康区间 1.4~2.2)" % density)
    if g.get("crossEdges"):
        print("跨科边: %d" % len(g["crossEdges"]))
    ok = dup == 0 and not dangling and 1.0 <= density <= 3.0
    print("\n体检: %s" % ("通过" if ok else "需要关注"))
    return 0 if ok else 1


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="ntg", description="把课程笔记/讲义变成知识图谱与双链复习库")
    ap.add_argument("--config", help="配置文件路径 (ntg.yaml)")
    ap.add_argument("--workdir", help="输出目录")
    ap.add_argument("--device", help="embedding 设备: cuda / cpu / mps")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="单科目构图")
    b.add_argument("source", help="输入：md 目录 / md 文件 / HTML 课程页 / .sdpub")
    b.add_argument("--name", help="科目名（用于标题与 MOC）")
    b.add_argument("--tag", help="输出文件名标识")
    b.add_argument("--subtitle", default="", help="副标题，如教师/班次")
    b.add_argument("--title", help="HTML 标题")
    b.add_argument("--domain", default="本课程", help="提示词里的领域，帮助模型判断重要性")
    b.add_argument("--api-key", help="覆盖配置里的 API key")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("collect", help="多科目合集 + 跨科关联")
    c.add_argument("manifest", help="科目清单 (yaml/json)")
    c.add_argument("--title", help="合集 HTML 标题")
    c.add_argument("--api-key")
    c.set_defaults(func=cmd_collect)

    o = sub.add_parser("obsidian", help="图谱 JSON → Obsidian 笔记库")
    o.add_argument("graph", help="graph_data_*.json")
    o.add_argument("--vault", help="Obsidian 库根目录")
    o.add_argument("--subdir", default="知识图谱", help="库内子目录名")
    o.add_argument("--no-clean", action="store_true", help="不删除已有目录")
    o.set_defaults(func=cmd_obsidian)

    k = sub.add_parser("check", help="数据体检")
    k.add_argument("graph", help="graph_data_*.json")
    k.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断（缓存的讲次不会丢，重跑会跳过）")
        return 130
    except Exception as e:  # noqa: BLE001
        print("错误: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
