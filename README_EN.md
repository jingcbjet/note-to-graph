# note-to-graph

Turn course notes, lecture handouts, and online-class transcripts into an
**interactive knowledge graph** and an **Obsidian double-linked study vault**.

```
your notes  ──►  knowledge graph HTML (for exploring)  ──►  Obsidian vault (for studying)
```

## Why it's fast and cheap

Most "build a knowledge graph with an LLM" approaches hand everything to the
model — chunking, concept extraction, relation judgement, scoring. That costs
three to four minutes per chapter and burns a lot of tokens.

The core insight here: **only one of those jobs actually needs a model.**

| Stage | Method | Cost | Time |
|---|---|---|---|
| ① Chunk | Local markdown heading rules | free | seconds |
| ② Extract concepts | One LLM call | token cost | ~45–90s / lecture |
| ③ Link edges | Local embeddings (cosine) | **free** | 0.5s / lecture |

"Which concepts does this passage discuss" genuinely requires a model. But
"which two concepts are related" is better answered by vector distance — more
consistent than asking a model, and free. Chunking has a deterministic correct
answer, so delegating it to a model only makes results non-reproducible and
breaks downstream comparison.

Measured on an RTX 3060 6GB with 4-way concurrency:

| Corpus | Lectures | Nodes | Edges | Time |
|---|---|---|---|---|
| Regulations & Policy | 52 | 1562 | 3199 | ~10 min |
| Social Work Practice | 63 | 1919 | 3130 | ~16 min |
| Comprehensive Ability | 12 | 410 | 594 | ~5.5 min |

Roughly 3–4× faster than an all-LLM pipeline, at about one third the cost.

## Install

```bash
git clone https://github.com/jingcbjet/note-to-graph.git
cd note-to-graph

python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# With an NVIDIA GPU, install torch — edge computation gets 10x+ faster
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

The first run downloads `BAAI/bge-large-zh-v1.5` (~1.3GB). If the download is
slow, set `NTG_NETWORK__HF_ENDPOINT=https://hf-mirror.com`.

## Configure

```bash
cp ntg.example.yaml ntg.yaml
```

Prefer an environment variable over putting the key in a file:

```bash
export NTG_API_KEY=sk-xxxxxxxx          # macOS / Linux
setx NTG_API_KEY "sk-xxxxxxxx"          # Windows
```

Supported providers (`llm.provider`): `siliconflow` (default,
`deepseek-ai/DeepSeek-V3.2`), `deepseek`, `openai`, `ollama` (local models),
and `openai-compatible` for anything else.

## Quick start

### Single subject

```bash
ntg build ./my-notes --name "Social Work Practice" --subtitle "Spring 2026"
```

Accepted inputs:

| Form | Notes |
|---|---|
| Directory | Reads `.md` files; the number in the filename is the lecture number (`L01.md` and `01.md` both work) |
| Single `.md` | Treated as one lecture |
| HTML | Course page; lectures are pulled from the embedded `const DATA = [...]` |
| `.sdpub` | Zip archive containing `fragments/serial-N/fragment_*.json` |

Outputs land in `output/`: `graph_data_subject.json` and `subject.html`.

### Multi-subject collection with cross-subject links

```yaml
# subjects.yaml
subjects:
  - key: law
    name: Regulations
    source: ./notes/law
    color: "#3b5bdb"
  - key: practice
    name: Practice
    source: ./notes/practice
    color: "#0f8a5f"
```

```bash
ntg collect subjects.yaml
```

The cross-subject threshold is deliberately stricter than the in-subject one
(0.82 vs 0.60). A wrong in-subject edge is just visual noise; a wrong
cross-subject edge makes you draw a false analogy.

### Export to Obsidian

**A force-directed graph is not meant to be read linearly.** Hundreds of
scattered nodes with a dozen characters each won't stick. Graphs are for
building a global impression; studying needs notes you can read in order and
follow via links:

```bash
ntg obsidian output/graph_collection.json --vault ~/obsidian/study
```

```
knowledge-graph/
├── Notes/<subject>/Lecture NN.md   # main study material, ⭐ marks key points
├── Concepts/<concept>.md           # definition / appearances / related / cross-subject
├── MOC/<subject> MOC.md            # per-subject index
├── MOC/Key Points MOC.md           # importance-5 only, for exam cramming
└── Index.md
```

One deliberate trade-off: **not every concept gets its own file.** Creating
thousands of files drowns the vault and produces masses of grey broken links.
So there's an allowlist — concepts appearing across subjects or lectures
(natural hubs), plus high-importance, well-connected ones. Measured: 3704
concepts condensed to 966 entries. The rest stay fully readable inside the
lecture notes, they just don't get their own page.

A broken-link check runs after writing; the target is 0 broken, 0 self-links.

### Sanity check

```bash
ntg check output/graph_collection.json
```

```
nodes 3891 / edges 6923
duplicate ids: 0
dangling edges: 0
edge density: 1.78 per node  (healthy 1.4–2.2)
cross-subject edges: 67
```

## Commands

| Command | Purpose |
|---|---|
| `ntg build <input>` | Single subject: extract → link → graph HTML |
| `ntg collect <manifest>` | Multi-subject collection + cross-subject edges |
| `ntg obsidian <json>` | Graph JSON → Obsidian vault |
| `ntg check <json>` | Validate ids / dangling edges / edge density |

Common flags: `--config`, `--workdir`, `--device` (`cuda` / `cpu` / `mps`).

## Tuning

| Parameter | Default | Notes |
|---|---|---|
| `graph.top_k` | 3 | Outgoing edges per node. **Do not remove this cap** |
| `graph.adaptive_sim` | true | Per-lecture adaptive threshold — **keep this on** |
| `graph.min_sim` | 0.60 | Base in-subject cosine threshold |
| `graph.cross_min_sim` | 0.82 | Cross-subject threshold |
| `llm.concurrency` | 4 | Higher tends to hit rate limits |
| `llm.max_tokens` | 16000 | Reasoning models need headroom |
| `split.min_nodes` | 8 | Fewer than this counts as failure and retries |

Without the `top_k` cap, edges explode past 8 per node and the graph becomes
unreadable.

**On adaptive thresholds** (added after a real bug): bge's similarity
distribution varies wildly *between lectures*. In one 52-lecture corpus,
medians ranged from 0.49 to 0.69. A fixed 0.60 threshold yields 10.7
edges/node on some lectures (hairball) and 0.65 on others (too sparse).

With `adaptive_sim` on, the threshold is derived per lecture from its own
distribution. Measured effect: per-lecture density collapsed from **0.65–10.73
down to 1.40–1.93**.

Note that `top_k` caps *outgoing* edges, but edges are undirected and
deduplicated — a node passively accumulates edges from many neighbours. So
there's a separate `2*top_k` degree cap to prevent hub nodes (one node in the
real corpus reached degree 14, 4.7× the configured value).

## Incremental runs

Per-lecture results are cached in `output/cache/nodes_<tag>_L<num>.json`.

- Re-running skips already-cached lectures
- Delete one cache file to redo just that lecture — no cost for the others
- Changing downstream rendering never re-calls the LLM
- Interrupts are safe; completed work is already on disk

## Project layout

```
ntg/
├── config.py      defaults ← yaml ← env vars ← explicit args
├── loaders.py     md dir / file / HTML course page / .sdpub
├── splitter.py    local deterministic chunking
├── llm.py         concept extraction with retry
├── embed.py       local embedding edges (in-subject / cross-subject)
├── graph.py       multi-subject merge + HTML rendering
├── obsidian.py    three-layer vault + link validation
├── pipeline.py    orchestration
└── cli.py         command line
```

## FAQ

**Too many edges, unreadable graph** — lower `graph.top_k` or raise
`graph.min_sim`. Check density with `ntg check`; 1.4–2.2 looks best. If only
one lecture is a hairball, `adaptive_sim` is probably off.

**One lecture is much denser/sparser than the rest** — verify
`graph.adaptive_sim` is true. Similarity distributions vary a lot within a
corpus; a fixed threshold makes effective density differ by 10x+.

**Cross-subject links are all noise** — raise `graph.cross_min_sim` to 0.85–0.88.

**Lots of grey links in Obsidian** — that's the allowlist working as intended.
Creating a file for every concept makes the vault unusable.

**LLM returns truncated JSON** — raise `llm.max_tokens`. Reasoning models burn
completion budget on chain-of-thought.

**Network errors calling the LLM** — `network.clear_proxy: true` strips proxy
env vars. Many CI sandboxes inject fake proxies that break domestic APIs.

## License

MIT — see [LICENSE](LICENSE).
