# -*- coding: utf-8 -*-
"""note-to-graph —— 把课程笔记变成知识图谱与双链复习库。

三段式管线，只把「理解概念」这一件非模型不可的事交给 LLM：
    ① 切分  本地 markdown 标题规则      （免费、可复现）
    ② 抽节点 LLM 一次调用              （约 45-90s/讲）
    ③ 算边  本地 bge embedding          （免费、GPU 上 0.5s/讲）
"""
__version__ = "0.1.0"

from .config import load_config, setup_env, resolve_api_key  # noqa: F401
from .pipeline import Pipeline  # noqa: F401
from .obsidian import ObsidianBuilder  # noqa: F401

__all__ = ["load_config", "setup_env", "resolve_api_key",
           "Pipeline", "ObsidianBuilder", "__version__"]
