# note-to-graph

[![CI](https://github.com/jingcbjet/note-to-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/jingcbjet/note-to-graph/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

[English](README_EN.md) | 中文

把课程笔记、讲义、网课文字稿变成**可交互知识图谱**和**Obsidian 双链复习库**。

输入一堆 markdown 或一个课程页 HTML，输出一张能点开探索的概念网络图，外加一套
可以直接在 Obsidian 里复习的笔记库。

```
你的笔记  ──►  知识图谱 HTML（探索用）  ──►  Obsidian 笔记库（复习用）
```

## 为什么快，为什么便宜

大多数"用 LLM 做知识图谱"的做法是把切分、抽概念、判断关联、打分全塞给模型，
结果每章要跑三四分钟，token 也烧得厉害。

这个项目的核心判断是：**这些活里只有一件非模型不可**。

| 阶段 | 手段 | 成本 | 耗时 |
|---|---|---|---|
| ① 切分 | 本地 markdown 标题规则 | 免费 | 秒级 |
| ② 抽概念 | LLM 一次调用 | 有 token 成本 | 约 45–90 秒/讲 |
| ③ 连边 | 本地 embedding（余弦相似度） | **免费** | 0.5 秒/讲 |

"这段话在讲什么概念"必须模型来读；但"哪两个概念相关"用向量距离算比让模型判断
更稳、更一致、而且不要钱。切分更是有确定答案的事，交给模型反而每次结果都不一样，
下游没法比较。

实测规模（RTX 3060 6GB，4 路并发）：

| 语料 | 讲数 | 节点 | 边 | 耗时 |
|---|---|---|---|---|
| 法规与政策 | 52 | 1562 | 3199 | 约 10 分钟 |
| 社会工作实务 | 63 | 1919 | 3130 | 约 16 分钟 |
| 综合能力 | 12 | 410 | 594 | 约 5.5 分钟 |

比模型全包的方案快 3–4 倍，成本约为其 1/3。

## 安装

```bash
git clone https://github.com/jingcbjet/note-to-graph.git
cd note-to-graph

python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 有 NVIDIA 显卡的话装上 torch，算边会快十倍以上
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

第一次运行会自动下载 `BAAI/bge-large-zh-v1.5`（约 1.3GB）。国内网络慢就设
`NTG_NETWORK__HF_ENDPOINT=https://hf-mirror.com`。

## 配置

复制配置模板，把你自己的 API key 填进去：

```bash
cp ntg.example.yaml ntg.yaml
```

推荐把 key 放环境变量，别写进文件：

```bash
export NTG_API_KEY=sk-xxxxxxxx          # macOS / Linux
setx NTG_API_KEY "sk-xxxxxxxx"          # Windows
```

支持的供应商（`llm.provider`）：

| provider | 说明 |
|---|---|
| `siliconflow` | 硅基流动，默认 `deepseek-ai/DeepSeek-V3.2` |
| `deepseek` | 官方 `deepseek-chat` |
| `openai` | `gpt-4o-mini` |
| `ollama` | 本地模型，如 `qwen2.5:7b` |
| `openai-compatible` | 任何兼容 OpenAI 接口的服务，手填 `base_url` + `model` |

## 快速开始

### 单科目

```bash
ntg build ./我的笔记 --name "社会工作实务" --subtitle "2026 春季班"
```

输入可以是：

| 形态 | 说明 |
|---|---|
| 目录 | 里面的 `.md`，文件名里的数字当讲号（`L01.md` / `01.md` 都认） |
| 单个 `.md` | 当作一讲处理 |
| HTML | 课程页，自动从内嵌的 `const DATA = [...]` 里抽讲次 |
| `.sdpub` | zip 归档，含 `fragments/serial-N/fragment_*.json` |

产物在 `output/`：`graph_data_subject.json` 和 `subject.html`。

### 多科目合集 + 跨科关联

把几个科目的图合成一张，并自动找出跨科目的关联概念：

```yaml
# subjects.yaml
subjects:
  - key: law
    name: 法规与政策
    subtitle: 杨老师精讲
    source: ./notes/law
    color: "#3b5bdb"
  - key: practice
    name: 社会工作实务
    source: ./notes/practice
    color: "#0f8a5f"
  - key: ability
    name: 综合能力
    source: ./notes/ability
    color: "#c2571a"
```

```bash
ntg collect subjects.yaml
```

跨科边的门槛故意设得比科内高（0.82 vs 0.60）。科内连错只是图上有条多余的线，
跨科连错会让人建立错误的类比，代价高得多。

### 转 Obsidian 笔记库

**图谱不适合逐条读** —— 几百个节点散开、每条十几个字，看完记不住。它适合建立
全局印象和发现意外关联。要复习得转成能线性读、能双链跳的笔记：

```bash
ntg obsidian output/graph_collection.json --vault "E:\文件\obsidian\备考"
```

产出三层结构：

```
知识图谱/
├── Notes/<科目>/第NN讲 X.md     # 按讲复盘的主读物，⭐ 标核心考点
├── Concepts/<概念>.md           # 概念词条，含定义/出现位置/相关概念/跨科关联
├── MOC/<科目> MOC.md            # 各科索引
├── MOC/高频考点 MOC.md          # 只收重要度 5，考前突击
└── 总索引.md                    # 入口
```

有个关键取舍：**不是所有概念都建文件**。全量几千个概念每人一个文件会把库淹没，
还会产生大量指向不存在页面的灰色空链接。所以走白名单——跨科目/跨讲出现的概念
（天然枢纽），加上重要度为 5 且连接数高的概念（考点中心）。实测 3704 个概念收敛到
966 个词条，其余概念仍在讲次笔记里完整可读，只是不单独建页。

写入后会自动做断链自检，目标是 0 断链 0 自指。

### 数据体检

```bash
ntg check output/graph_collection.json
```

```
节点 3891 / 边 6923
id 重复: 0
悬空边: 0
边密度: 1.78 边/节点  (健康区间 1.4~2.2)
跨科边: 67

体检: 通过
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `ntg build <输入>` | 单科目：抽取 → 算边 → 图谱 HTML |
| `ntg collect <清单>` | 多科目合集 + 跨科关联 |
| `ntg obsidian <json>` | 图谱 JSON → Obsidian 笔记库 |
| `ntg check <json>` | 体检：id 唯一性 / 悬空边 / 边密度 |

通用参数：`--config`、`--workdir`、`--device`（`cuda` / `cpu` / `mps`）。

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `graph.top_k` | 3 | 每节点向外保留几条边。**别去掉这个限制** |
| `graph.adaptive_sim` | true | 按每讲相似度分布自适应调门槛，**建议保持开启** |
| `graph.min_sim` | 0.60 | 科内余弦门槛（自适应模式下的基准值） |
| `graph.target_density` | 0 | 目标边密度，0 = 自动（夹在 1.4~2.2） |
| `graph.cross_min_sim` | 0.82 | 跨科门槛 |
| `llm.concurrency` | 4 | 并发，调高容易触发限速 |
| `llm.max_tokens` | 16000 | 推理型模型要给足，否则长 JSON 被截断 |
| `split.min_nodes` | 8 | 单讲产出少于该数视为失败并重试 |

不加 `top_k` 限制的话边会爆炸到 8 条/节点以上，图糊成一团没法看。

**关于自适应门槛**（踩过坑才加的）：bge 的相似度分布在**不同讲次之间差异极大**
——同一批 52 讲语料里，有的讲中位数 0.69，有的只有 0.49。用固定的 0.60 门槛会
导致某些讲连出 10.7 条边/节点（毛球），另一些只有 0.65 条（太稀）。

开启 `adaptive_sim` 后按每讲的分布取分位数，实测单讲密度从 **0.65–10.73 收敛到
1.40–1.93**，全科稳定在 1.7 边/节点：

```
固定门槛 0.60        自适应门槛
  0.65 ~ 10.73   →    1.40 ~ 1.93
```

另外注意 `top_k` 限制的是"每节点向外发几条"，而边是无向去重的，一个节点会从
多个邻居那里被动收到边。所以内部另有一个 `2*top_k` 的度数封顶，用来挡住
超级枢纽（实测真实语料里出现过度数 14 的节点，是配置值的 4.7 倍）。

## 增量与重跑

每讲的抽取结果缓存在 `output/cache/nodes_<tag>_L<讲号>.json`。

- 重跑会自动跳过已缓存的讲次
- 想重做某一讲，删掉对应缓存文件即可，**不会重花其他讲的钱**
- 改下游渲染逻辑（图谱样式、Obsidian 结构）完全不用重新调 LLM
- 中断随时可续，已验证的结果都落盘了

## 项目结构

```
ntg/
├── config.py      配置层（默认值 ← yaml ← 环境变量 ← 显式传参）
├── loaders.py     输入适配：md 目录/单文件/HTML 课程页/.sdpub
├── splitter.py    本地确定性切分
├── llm.py         节点抽取 + 重试
├── embed.py       本地 embedding 算边（科内 / 跨科）
├── graph.py       多科目合并 + HTML 渲染
├── obsidian.py    三层笔记库生成 + 断链自检
├── pipeline.py    主流程编排
└── cli.py         命令行
```

## 常见问题

**边太多，图糊成一团**
调低 `graph.top_k`（比如 2）或调高 `graph.min_sim`。用 `ntg check` 看边密度，
落在 1.4–2.2 之间比较好看。如果只有某一讲糊，多半是自适应门槛被关掉了。

**某一讲特别糊 / 特别空**
检查 `graph.adaptive_sim` 是否为 true。相似度分布在本语料内差异很大，
固定门槛会让不同讲次的实际密度相差十几倍。

**跨科关联全是噪声**
调高 `graph.cross_min_sim`（0.85–0.88）。跨科本来就该严。

**Obsidian 里一堆灰色链接**
检查是不是关闭了白名单。默认只给高价值概念建词条，这是有意的——全量建文件
反而让库不可用。

**模型返回的 JSON 被截断**
调大 `llm.max_tokens`。推理型模型（如 DeepSeek-V3.2）的思维链会吃掉大量
completion 额度，给 16000 是为了留余量。

**调用 LLM 报网络错误**
配置里 `network.clear_proxy: true` 会清掉代理环境变量。很多 CI/沙箱会注入假
代理，导致国内 API 连不上。

**embedding 模型下载卡住**
设 `NTG_NETWORK__HF_ENDPOINT=https://hf-mirror.com`，或提前把模型放到
`~/.cache/huggingface/`。

## 许可

MIT，见 [LICENSE](LICENSE)。
