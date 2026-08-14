# 知识模型

## 1. 目的

本文档定义 ZGLab RAG 中每个文档和 Chunk 必须携带的最小元数据集合。

目标是让检索过程能够识别身份、项目、知识范围、时效性、来源追踪以及公开/私有边界。

## 2. 文档元数据

推荐的文档级字段：

```yaml
document_id: string
source_id: string
source_kind: local | git | web | generated
scope: identity | project | knowledge | experience | dynamic
visibility: public | private
priority: integer
path: string
title: string
summary: string | null
tags: [string]
project: string | null
language: zh-CN | en | mixed
source_url: string | null
source_revision: string | null
content_hash: string
created_at: datetime | null
updated_at: datetime | null
ingested_at: datetime
```

### document_id

源文档的稳定逻辑标识符。

同一来源重复同步时，应优先使用由来源和路径生成的确定性 ID，而不是随机 UUID。

示例：

```text
notes:knowledge/agent-long-term-memory.md
```

### source_id

必须匹配 `config/sources.yaml` 中的一条配置。

示例：

```text
identity-profile
notes
zglab-website
resume-tailor
```

### scope

定义文档的语义角色。

#### identity

稳定的个人事实和个人定位。

#### project

项目实现、架构和设计决策。

#### knowledge

可复用的技术知识、问题复盘和方法论。

#### experience

教育、实习、论文、奖项及其他事实性经历。

#### dynamic

频繁变化的来源材料。

### visibility

强制安全边界。

对于公网助手：

```text
进入排序或 Context 构建前，visibility 必须等于 public
```

`visibility` 不是可参与软排序的特征。

### priority

来源权威性的提示，不可替代相关性。

建议的初始等级：

```text
100  权威身份事实
90   官方项目文档 / 网站结构化数据
80   经过整理的 Notes
70   其他公开项目文档
```

`priority` 可以作为同分处理或路由提示，但不能强行让不相关的身份 Chunk 排在相关项目证据之前。

## 3. Chunk 元数据

每个 Chunk 应保留：

```yaml
chunk_id: string
document_id: string
source_id: string
scope: string
visibility: string
priority: integer
title: string
section_path: [string]
chunk_index: integer
content: string
content_hash: string
token_count: integer | null
char_count: integer
project: string | null
tags: [string]
source_url: string | null
source_path: string
revision: string | null
```

Chunk 有意重复保存 `visibility`、`scope` 和核心来源字段，以便检索在执行高成本的排序或
Context 构建前完成过滤。

`chunk_id` 由文档标识、标题路径、章节出现次序、超长章节分段序号和 Chunk 内容哈希
确定性生成。因此，对未变化的内容重复执行 ingestion 会产生相同 ID。来源存在 revision 时，
由 `revision` 保存；本地维护的文档可以为 null。

## 4. Markdown 结构感知切分

不要从无差别的固定字符切分开始。

推荐流程：

1. 解析 frontmatter；
2. 按 Markdown 标题划分章节；
3. 将标题层级保留为 `section_path`；
4. 合适时合并相邻的短章节；
5. 对超长章节使用 overlap 二次切分；
6. Chunk 不得脱离标题或章节上下文。

源文档示例：

```markdown
# Agent Memory

## Working Memory
...

## Long-Term Memory
...
```

Chunk 应携带：

```yaml
title: Agent Memory
section_path:
  - Long-Term Memory
```

检索文本可以在内部添加标题和章节上下文，但存储的 `content` 应忠实保留来源正文。

## 5. Frontmatter

本地维护的文档可以选择使用：

```yaml
---
title: Example
scope: knowledge
visibility: public
tags:
  - RAG
  - Agent
project: null
---
```

来源注册表提供默认值。文档 frontmatter 可以补充元数据，但不得把 private 静默提升为 public。

推荐规则：

```text
最终 visibility = 来源 visibility 与文档 visibility 中限制更严格的一方
```

## 6. 来源追踪

每个可用于回答的 Chunk 都必须能够追溯到源文档。

至少保留：

- source ID；
- 文档路径；
- 来源 URL（如果有）；
- Git 来源的 commit SHA/revision（如果有）；
- 章节路径。

最终回答的引用层不得生成无法映射回这些元数据的来源标签。

## 7. 冲突处理

文档内容不一致时：

1. ingestion 时保留双方事实；
2. 不要为了统一结论而改写来源；
3. 在检索或生成阶段比较 scope、priority、来源权威性和时效性；
4. 当前状态问题优先采用权威的结构化身份或项目来源；
5. 证据不足时，明确说明冲突仍未解决。

示例：

```text
旧项目笔记：status = building
新官方项目元数据：status = completed
```

对于当前状态问题，通常应采用更新的官方项目来源；旧笔记仍可用于回答历史问题。

## 8. 来源类型

### 本地维护的知识

示例：

- `knowledge/identity/profile.md`；
- 未来维护的经历文档。

这些是专门为助手维护的高权威来源。

### Git 仓库文档

只有匹配已配置 include 规则的文件才允许进入系统。

每个 Git 来源声明一个相对于项目根目录的 `local_path`。发现过程必须是确定性的、经过
去重的，并且仅允许 include allowlist 匹配的普通 Markdown 文件；exclude 规则优先。
不得跟随指向仓库外部的符号链接。

Git 来源文档使用仓库相对路径作为 `source_path`，并将当前本地 HEAD SHA 保存为 revision。
不得把与机器相关的绝对 checkout 路径写入文档或 Chunk 元数据。

适合默认纳入的内容：

- README；
- `docs/**/*.md`；
- 架构或设计 Markdown；
- 选定的项目知识文件。

不适合默认纳入的内容：

- 源代码；
- lock 文件；
- 生成文件；
- 二进制文件；
- `.env`；
- tests/fixtures，除非明确有价值。

## 9. 公网助手回答契约

对于事实性回答，generation 层应接收：

```text
问题
Persona 规则
选定的公开证据
来源元数据
```

它不应接收无关的私有文档，也不得把 profile 或 Persona 文本当作编造细节的许可。

如果证据不足，预期行为是给出有边界的回答，例如：

```text
我目前公开的资料里没有足够信息确认这一点。
```

而不是推测。
