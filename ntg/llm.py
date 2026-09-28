# -*- coding: utf-8 -*-
"""LLM 节点抽取：把正文切成块，让模型只输出 {label, content, importance}。

设计要点：**不让 LLM 干它能被本地代码干好的活**。切分靠 markdown 标题规则、
算边靠本地 embedding，LLM 只负责"读懂这段话说的是什么概念"这一件非它不可的事。
这是本管线比"LLM 全包"快 3-4 倍、成本约 1/3 的根本原因。
"""
import json
import time

import requests

# 提示词模板。注意 JSON 花括号必须双写，因为后面走 .format()
PROMPT_TMPL = """你是{domain}的知识点整理员。下面是《{course}》某一讲的课程笔记（markdown）。请提取其中的知识点，输出 JSON。

要求：
1. 每个知识点是一个节点：label 为概念/制度/术语名（不超过15字），content 为一句话概括+关键数字/标准（不超过100字），importance 为 1-5（5=明确标注考点或核心内容，1=背景信息）。
2. 知识点要互不重复、覆盖全篇，数量 {min_nodes}~{max_nodes} 个。
3. 只依据原文，不要编造。
4. 严格输出 JSON 对象：{{"nodes": [{{"label": "...", "content": "...", "importance": 3}}]}}，不要输出其他文字。

课程笔记标题：{title}

笔记正文：
{body}"""


class LLMError(RuntimeError):
    pass


def extract_nodes(title, body, cfg, api_key, domain="本课程", course="课程笔记"):
    """调一次 LLM，返回 (nodes, usage)。

    nodes: [{"label": str, "content": str, "importance": int}, ...]
    """
    llm = cfg["llm"]
    sp = cfg["split"]
    prompt = PROMPT_TMPL.format(
        title=title, body=body, domain=domain, course=course,
        min_nodes=sp.get("min_nodes", 8) * 2,
        max_nodes=35,
    )
    payload = {
        "model": llm["model"],
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "max_tokens": llm.get("max_tokens", 16000),
        "temperature": llm.get("temperature", 0.2),
    }
    url = llm["base_url"].rstrip("/") + "/chat/completions"
    r = requests.post(
        url,
        json=payload,
        headers={"Authorization": "Bearer " + api_key},
        timeout=llm.get("timeout", 300),
    )
    if r.status_code >= 400:
        raise LLMError("HTTP %s: %s" % (r.status_code, r.text[:300]))
    data = r.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMError("响应结构异常: %s" % json.dumps(data, ensure_ascii=False)[:300]) from e

    usage = data.get("usage", {})
    try:
        nodes = json.loads(text)["nodes"]
    except (ValueError, KeyError) as e:
        raise LLMError("模型未返回合法 JSON: %s" % text[:300]) from e

    cleaned = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        label = str(n.get("label") or "").strip()
        content = str(n.get("content") or "").strip()
        if not label or not content:
            continue
        try:
            imp = int(n.get("importance") or 3)
        except (TypeError, ValueError):
            imp = 3
        cleaned.append({
            "label": label[:60],
            "content": content[:400],
            "importance": max(1, min(5, imp)),
        })
    return cleaned, usage


def extract_with_retry(title, body, cfg, api_key, domain="本课程", course="课程笔记",
                       max_attempts=4, log=print):
    """带指数退避的重试包装。产出太少也视为失败——通常是模型截断或跑偏。"""
    min_nodes = cfg["split"].get("min_nodes", 8)
    last_err = None
    for attempt in range(max_attempts):
        t0 = time.time()
        try:
            nodes, usage = extract_nodes(title, body, cfg, api_key, domain, course)
            dt = time.time() - t0
            if len(nodes) < min_nodes:
                raise LLMError("产出节点过少 (%d < %d)" % (len(nodes), min_nodes))
            return nodes, usage, dt
        except Exception as e:  # noqa: BLE001 — 网络/解析/限流都要重试
            last_err = e
            wait = 10 * (attempt + 1)
            log("  第 %d 次失败: %s -> %ds 后重试" % (attempt + 1, e, wait))
            if attempt < max_attempts - 1:
                time.sleep(wait)
    raise LLMError("重试 %d 次仍失败: %s" % (max_attempts, last_err))
