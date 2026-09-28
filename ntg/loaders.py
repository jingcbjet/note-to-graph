# -*- coding: utf-8 -*-
"""输入适配：markdown 目录 / 单文件 / HTML 课程页 / .sdpub 归档 → 统一的讲次列表。

统一输出结构（Lecture）：
    {"num": int, "title": str, "text": str, "source": str}
"""
import io
import json
import os
import re
import zipfile

LECTURE = dict


def _lec(num, title, text, source):
    return {"num": int(num), "title": str(title or "第%02d讲" % int(num)),
            "text": text, "source": source}


# ---------------------------------------------------------------- markdown

def load_md_dir(path, pattern=r"^L?(\d+)\.md$", recursive=False):
    """读一个目录下的 markdown，文件名里的数字当讲号。

    pattern 默认匹配 L01.md / 01.md；用 --pattern 可自定义。
    """
    rx = re.compile(pattern, re.I)
    items = []
    walker = os.walk(path) if recursive else [(path, [], os.listdir(path))]
    for root, _dirs, files in walker:
        for fn in sorted(files):
            m = rx.match(fn)
            if not m:
                continue
            fp = os.path.join(root, fn)
            text = io.open(fp, encoding="utf-8", errors="replace").read()
            title = _title_from_md(text) or os.path.splitext(fn)[0]
            items.append(_lec(int(m.group(1)), title, text, fp))
    items.sort(key=lambda x: x["num"])
    return items


def _title_from_md(text):
    m = re.search(r"^#\s+第?\s*(\d+)\s*讲\s*(.*)$", text, re.M)
    if m:
        return ("第%s讲 %s" % (m.group(1), m.group(2))).strip()
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def load_md_file(path, num=1):
    text = io.open(path, encoding="utf-8", errors="replace").read()
    title = _title_from_md(text) or os.path.splitext(os.path.basename(path))[0]
    return [_lec(num, title, text, path)]


# ---------------------------------------------------------------- HTML

def html_to_md(h):
    """把一小段 HTML 转成 markdown。够用就好，不追求完整解析。"""
    t = h or ""
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"</(h[1-6]|p|li|blockquote|div|tr)>", "\n", t)
    for i in range(1, 7):
        t = re.sub(r"<h%d[^>]*>" % i, "#" * i + " ", t)
    t = re.sub(r"<li[^>]*>", "- ", t)
    t = re.sub(r"<(strong|b)[^>]*>", "**", t)
    t = re.sub(r"</(strong|b)>", "**", t)
    t = re.sub(r"<(em|i)[^>]*>", "*", t)
    t = re.sub(r"</(em|i)>", "*", t)
    t = re.sub(r"</(td|th)>", " | ", t)
    t = re.sub(r"<tr[^>]*>", "| ", t)
    t = re.sub(r"<[^>]+>", "", t)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        t = t.replace(a, b)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# 课程页常把全部内容塞进一个 JS 数组字面量
DATA_RE = re.compile(
    r"const\s+DATA\s*=\s*(\[.*?\])\s*;?\s*"
    r"(?:const|function|let|var|//|/\*|\$|document|window|\Z)",
    re.S,
)


def load_course_html(path, num_key="num", title_key="title", html_key="html"):
    """从课程页 HTML 里抽出内嵌的讲次数据。

    这类页面（很多在线课程平台导出都是这个形态）把内容写成：
        const DATA = [{"course": "...", "items": [{"num","title","html"}]}]
    """
    raw = io.open(path, encoding="utf-8", errors="replace").read()
    m = DATA_RE.search(raw)
    if not m:
        # 退化：整个文件当一讲
        return [_lec(1, os.path.splitext(os.path.basename(path))[0],
                     html_to_md(raw), path)]
    try:
        data = json.loads(m.group(1))
    except ValueError as e:
        raise RuntimeError("课程页里的 DATA 不是合法 JSON（可能是页面结构变了）") from e

    items = []
    if isinstance(data, list) and data and isinstance(data[0], dict) and "items" in data[0]:
        items = data[0]["items"]
    elif isinstance(data, list):
        items = data

    out = []
    for it in items:
        num = it.get(num_key)
        try:
            num = int(num)
        except (TypeError, ValueError):
            continue
        title = str(it.get(title_key) or "")
        # 标题形如 "17 · 医疗救助" → 只留后半段
        if "· " in title:
            title = title.split("· ", 1)[-1]
        body = html_to_md(it.get(html_key) or "")
        md = "# 第%02d讲 %s\n\n%s" % (num, title, body)
        # 原文自带 H1 会和文件头重复，把第二个降为 H2
        lines = md.split("\n")
        h1 = [i for i, l in enumerate(lines) if l.startswith("# ")]
        if len(h1) > 1:
            lines[h1[1]] = "##" + lines[h1[1]][1:]
        md = "\n".join(lines)
        out.append(_lec(num, "第%02d讲 %s" % (num, title), md, path))
    out.sort(key=lambda x: x["num"])
    return out


# ---------------------------------------------------------------- .sdpub

def load_sdpub(path, serial_map=None):
    """读 .sdpub 归档（zip + fragments/serial-N/fragment_*.json）。

    serial_map: {讲号: serialId}，不传则按 serial-N 里的 N 当讲号。
    """
    out = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        serials = sorted({
            int(m.group(1)) for m in
            (re.search(r"fragments/serial-(\d+)/", n) for n in names) if m
        })
        for sid in serials:
            prefix = "fragments/serial-%d/" % sid
            frags = sorted(
                (n for n in names if n.startswith(prefix)),
                key=lambda n: int(re.search(r"fragment_(\d+)", n).group(1)
                                  if re.search(r"fragment_(\d+)", n) else 0),
            )
            parts = []
            for n in frags:
                try:
                    d = json.loads(z.read(n))
                except ValueError:
                    continue
                for s in d.get("sentences", []):
                    t = (s.get("text") or "").strip()
                    if t:
                        parts.append(t)
            if not parts:
                continue
            num = (serial_map or {}).get(sid, sid)
            out.append(_lec(num, "第%02d讲" % num, "\n".join(parts), "%s#%d" % (path, sid)))
    out.sort(key=lambda x: x["num"])
    return out


# ---------------------------------------------------------------- 自动识别

def load_lectures(path, pattern=None, serial_map=None):
    """按输入形态自动选择 loader。"""
    if os.path.isdir(path):
        return load_md_dir(path, pattern=pattern or r"^L?(\d+)\.md$")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".sdpub":
        return load_sdpub(path, serial_map)
    if ext in (".html", ".htm"):
        return load_course_html(path)
    if ext in (".md", ".markdown", ".txt"):
        return load_md_file(path)
    raise RuntimeError("无法识别的输入类型: %s" % path)
