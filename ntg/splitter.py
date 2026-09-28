# -*- coding: utf-8 -*-
"""本地确定性切分：只按 markdown 标题规则切块，零成本、零延迟、结果可复现。

一开始我试过让 LLM 顺手做切分，结果是慢且不稳定——同一段文本两次跑出来的
块边界可能不同，导致下游节点和边无法比较。切分这种有确定答案的活就不该给模型。
"""
import re


def split_blocks(md_text, min_merge=120, max_cap=900):
    """按标题切块，再修正两种极端：太碎的小块、太大的巨块。

    返回 [(title, body), ...]
    """
    lines = md_text.split("\n")
    blocks = []
    cur_title, cur = "", []
    for ln in lines:
        if re.match(r"^#{1,4}\s", ln):
            if cur:
                blocks.append((cur_title, "\n".join(cur).strip()))
            cur_title, cur = ln.strip("# \n"), [ln]
        else:
            cur.append(ln)
    if cur:
        blocks.append((cur_title, "\n".join(cur).strip()))
    blocks = [(t, b) for t, b in blocks if b]

    # 小块并入前一块：标题下只有一行字的情况很常见
    merged = []
    for t, b in blocks:
        if merged and len(b) < min_merge:
            pt, pb = merged[-1]
            merged[-1] = (pt or t, pb + "\n" + b)
        else:
            merged.append((t, b))

    # 超大块按空行再切，避免超出模型有效上下文
    final = []
    for t, b in merged:
        if len(b) <= max_cap:
            final.append((t, b))
            continue
        paras = re.split(r"\n\s*\n", b)
        buf, blen = [], 0
        for p in paras:
            if buf and blen + len(p) > max_cap:
                final.append((t, "\n\n".join(buf)))
                buf, blen = [p], len(p)
            else:
                buf.append(p)
                blen += len(p)
        if buf:
            final.append((t, "\n\n".join(buf)))
    return final
