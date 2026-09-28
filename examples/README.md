# 示例用法

## 最小可跑示例

仓库自带两讲示例笔记（`sample-notes/`），可以直接跑通全流程：

```bash
# 1. 单科目构图
ntg build examples/sample-notes --name "示例课程" --tag demo

# 产物
#   output/graph_data_demo.json
#   output/demo.html          ← 用浏览器打开
```

约 1–2 分钟（取决于网络和是否有 GPU）。

```bash
# 2. 转成 Obsidian 笔记库
ntg obsidian output/graph_data_demo.json --vault ./my-vault
```

```bash
# 3. 体检
ntg check output/graph_data_demo.json
```

## 多科目合集示例

`subjects.example.yaml` 演示怎么把三个科目合成一张图：

```bash
cp examples/subjects.example.yaml subjects.yaml
# 改里面的 source 指向你自己的笔记目录
ntg collect subjects.yaml
```

## 输入格式对照

### 目录（推荐）

```
my-notes/
├── L01.md
├── L02.md
└── L03.md
```

```bash
ntg build my-notes --name "课程名"
```

文件名里的数字当作讲号。`L01.md`、`01.md`、`第01讲.md` 都能识别，
自定义的话用 `--pattern`。

### 单文件

```bash
ntg build note.md --name "单讲测试"
```

### 课程页 HTML

很多在线课程平台导出的页面会把全部内容塞进一个 JS 数组：

```html
<script>
const DATA = [{"course": "...", "items": [
  {"num": "1", "title": "1 · 绪论", "html": "<p>...</p>"}
]}];
</script>
```

这种页面可以直接喂进去：

```bash
ntg build course.html --name "课程名"
```

### .sdpub 归档

如果内容在 zip 归档里（含 `fragments/serial-N/fragment_*.json`）：

```bash
ntg build archive.sdpub --name "课程名"
```

## 笔记写得越结构化，效果越好

模型是从标题层级理解内容结构的，所以：

**推荐**

```markdown
## 增能理论

一句话说明这个理论是什么。

### 增能层级

个人层面、人际层面、社会层面。
```

**不推荐**

```markdown
## 内容

今天我们来讲增能理论。这个理论呢它其实分好几个层面，首先个人层面…
（几百字不分段，没有子标题）
```

第二种情况切分会退化——整讲一个大块，模型容易漏掉后半段内容。
如果原始讲义就是这样的，建议先手工加一层标题，或者接受产出质量下降。

## 给模型领域提示

`--domain` 会写进提示词，帮助模型判断哪些内容是重点：

```bash
ntg build notes --name "社会工作实务" --domain "社会工作职业水平考试"
```

不填的话默认「本课程」，效果通常也够用。
