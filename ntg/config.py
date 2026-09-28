# -*- coding: utf-8 -*-
"""配置层：把原来散落在各脚本里的硬编码路径/常量收拢到一处。

优先级：显式传参 > 环境变量 > config.yaml > 内置默认值
"""
import io
import os
import re

try:
    import yaml
    _HAS_YAML = True
except ImportError:  # yaml 是可选依赖
    _HAS_YAML = False

DEFAULTS = {
    # --- LLM ---
    "llm": {
        "provider": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3.2",
        "api_key": "",          # 留空则读环境变量 NTG_API_KEY
        "max_tokens": 16000,
        "temperature": 0.2,
        "concurrency": 4,
        "timeout": 300,
    },
    # --- Embedding ---
    "embed": {
        "model": "BAAI/bge-large-zh-v1.5",
        "device": "cuda",       # cuda / cpu / mps
        "batch_size": 64,
    },
    # --- 构图参数 ---
    "graph": {
        "top_k": 3,             # 每个节点最多保留几条边
        "min_sim": 0.60,        # 科内余弦门槛（自适应模式下的基准值）
        "adaptive_sim": True,   # 按相似度分布自适应调门槛，避免讲次之间密度悬殊
        "target_density": 0,    # 目标边密度，0=自动 (top_k*0.7，夹在 1.4~2.2)
        "cross_min_sim": 0.82,  # 跨科门槛（误连代价更高，故更严）
        "cross_top_k": 3,
    },
    # --- 切分 ---
    "split": {
        "min_merge": 120,       # 小于此长度的块并入前一块
        "max_cap": 900,         # 超过此长度按空行二切
        "min_nodes": 8,         # 单讲产出少于该数视为失败并重试
    },
    # --- 输出 ---
    "output": {
        "workdir": "./output",
        "html_title": "知识图谱",
        "obsidian_vault": "",   # Obsidian 库根目录，留空则跳过该步
        "obsidian_subdir": "知识图谱",
    },
    # --- 网络 ---
    "network": {
        "clear_proxy": True,    # 调用 LLM 前清掉代理（沙箱假代理会让国内直连失败）
        "hf_endpoint": "",      # 如 https://hf-mirror.com，留空用官方
    },
}

# 支持的 LLM 供应商预设
PROVIDER_PRESETS = {
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3.2",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
    },
    "openai-compatible": {
        "base_url": "",
        "model": "",
    },
}

ENV_PREFIX = "NTG_"


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides():
    """NTG_LLM__MODEL / NTG_GRAPH__MIN_SIM 这种双下划线写法映射到嵌套键。"""
    out = {}
    for key, val in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX):].lower().split("__")
        if len(path) < 2:
            continue
        cur = out
        for p in path[:-1]:
            cur = cur.setdefault(p, {})
        cur[path[-1]] = _coerce(val)
    return out


def _coerce(v):
    if v.lower() in ("true", "yes", "1"):
        return True
    if v.lower() in ("false", "no", "0"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def load_config(path=None, overrides=None):
    """读取配置。path 为 None 时依次找 ./ntg.yaml、./config.yaml。"""
    cfg = dict(DEFAULTS)
    candidates = [path] if path else ["ntg.yaml", "config.yaml"]
    for c in candidates:
        if c and os.path.exists(c):
            raw = io.open(c, encoding="utf-8").read()
            if _HAS_YAML:
                data = yaml.safe_load(raw) or {}
            else:
                raise RuntimeError("读取 %s 需要 pyyaml：pip install pyyaml" % c)
            cfg = _deep_merge(cfg, data)
            break
    cfg = _deep_merge(cfg, _env_overrides())
    cfg = _deep_merge(cfg, overrides)
    return cfg


def clear_proxy_env():
    """清掉代理环境变量。

    很多 CI/沙箱会注入假代理，导致调用国内 LLM API 直接失败。
    """
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)


def force_utf8_stdio():
    """把 stdout/stderr 切到 UTF-8。

    本工具全程输出中文。Windows 上 Python 默认用 ANSI 代码页编码 stdout：
    中文系统的 GBK 恰好能编中文，所以本地不报错；**英文系统是 cp1252**，
    一打印中文就 `UnicodeEncodeError: 'charmap' codec can't encode ...` 崩掉。
    CI 的 windows-latest 正是这种情况。

    自带脚本建议开头调一次；库使用者从 `ntg.config` 导入即可。
    """
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 — 旧版本或被重定向的流不支持就算了
            pass


def setup_env(cfg):
    """按配置调整进程环境（代理、HF 镜像、CUDA 可见性）。"""
    if cfg["network"].get("clear_proxy"):
        clear_proxy_env()
    hf = cfg["network"].get("hf_endpoint")
    if hf:
        os.environ.setdefault("HF_ENDPOINT", hf)


def resolve_api_key(cfg, explicit=None):
    """取 API key：显式 > 配置 > NTG_API_KEY > OPENAI_API_KEY。"""
    if explicit:
        return explicit
    if cfg["llm"].get("api_key"):
        return cfg["llm"]["api_key"]
    for env in ("NTG_API_KEY", "OPENAI_API_KEY", "SILICONFLOW_API_KEY"):
        if os.environ.get(env):
            return os.environ[env]
    raise RuntimeError(
        "未找到 API key。请设置环境变量 NTG_API_KEY，或在 ntg.yaml 里填 llm.api_key"
    )


def apply_provider(cfg):
    """把 provider 预设合并进 llm 配置（用户显式填的优先）。"""
    name = cfg["llm"].get("provider", "siliconflow")
    preset = PROVIDER_PRESETS.get(name)
    if not preset:
        return cfg
    llm = cfg["llm"]
    if not llm.get("base_url"):
        llm["base_url"] = preset["base_url"]
    if not llm.get("model"):
        llm["model"] = preset["model"]
    return cfg


def safe_filename(s, maxlen=60):
    """Windows/Unix 通用安全文件名。"""
    s = re.sub(r'[\\/:*?"<>|#^\[\]]', "", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:maxlen] or "untitled"
