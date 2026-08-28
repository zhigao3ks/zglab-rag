# MCP Tool Runtime 设计（Phase 13）

> 权威 Phase 路线见 `docs/roadmap-v2.md`。本文是 Phase 13 的技术设计：Phase 13A 已落地，
> Phase 13B / 13C / 13D 仅设计、尚未实现。

## 0. 状态

```text
Phase 13A — Tool Core Boundary & MCP Contracts       ✅ 已完成（本文 §3–§10）
Phase 13B — MCP Server Runtime                       ⏳ 未开始（本文 §11）
Phase 13C — MCP Client + Capability Integration      ⏳ 未开始（本文 §12）
Phase 13D — Security / Evaluation / Production       ⏳ 未开始（本文 §13）
```

Phase 13 的总体目标不是“让 Agent 自动调用工具”，而是：

> 建立一个稳定、确定性、可审计、低副作用、机器可调用的 MCP Tool Runtime。

## 1. 为什么需要 MCP

Phase 11（Auth）与 Phase 12（PersonalKnowledgeSkill / WebResearchSkill）已经把“谁可以消费”
和“受控的完整能力边界”建立起来。Phase 13 解决的是第三类能力：**原子工具**。

| 概念 | 含义 | 现状 |
| --- | --- | --- |
| Skill / Capability | 服务端受控的完整业务管线（RAG / Research） | Phase 12 已落地 |
| Tool | 外部可调用的原子操作（JSON / Base64 / text / timestamp） | Phase 13 |

工具逻辑已经存在于独立的 `zglab-tools` 仓库（TypeScript，浏览器本地运行）。Phase 13 要做的
不是把它复制进 Python，而是通过 **MCP 协议**把它安全地暴露给 `zglab-rag`，让两种语言共享
同一份确定性实现，同时保持清晰的 process / 语言边界。

## 2. 总体架构（冻结）

```text
                    Shared Tool Core（zglab-tools/src/tool-core）
                    /                        \
                   /                          \
        Browser UI（tools.zglab.fun）        MCP Server（Phase 13B，stdio）
                                                   │
                                                   ▼ stdio
                                             MCP Client（zglab-rag，Phase 13C）
```

冻结为 **Option B**（MCP Server 放在 zglab-tools，通过 stdio 提供协议）：

- 不复制算法到 Python（拒绝 Option C）；
- Python 不 import Node package（拒绝 Option A 的直接依赖）；
- MCP 正好承担 process boundary，语言边界清晰；
- Browser UI 与 MCP Server 共用同一 TypeScript core。

## 3. Shared Tool Core（13A 已落地，位于 zglab-tools）

`zglab-tools` 新增 `src/tool-core/`，只 import 既有 `src/tools/*/logic.ts` 纯逻辑，不复制、
不重写：

```text
src/tool-core/
    contracts.ts      # ToolDefinition / ToolResult / JSON Schema / 错误码
    errors.ts         # ToolError / DuplicateToolError / ToolNotFoundError
    limits.ts         # 默认资源上限
    registry.ts       # ToolRegistry（显式 allowlist，无文件扫描）
    definitions.ts    # 10 个 deterministic pure tool 定义
    index.ts          # 公共导出
    *.test.ts         # Vitest 单元测试
```

浏览器工具站 `tools.zglab.fun` 的 UI 与行为零回归（现有 UI 测试全部通过）。

## 4. Tool Contract

`ToolDefinition` 至少表达：

```text
id, name, description
inputSchema（JSON Schema，additionalProperties:false，required/enum/长度显式）
outputSchema（JSON Schema）
sideEffect, networkAccess, deterministic
timeoutMs, maxInputBytes, maxOutputBytes
execute(input) -> output | Promise<output>
```

跨语言契约以 JSON Schema 为唯一真相（MCP `tools/list` / `tools/call` 直接消费），TypeScript
类型只是实现细节。工具 id 使用稳定 `snake_case`，不含 UI 名称、不依赖中文标题。

## 5. Tool Registry

```text
register（重复 id → DuplicateToolError）
get / has / list（未知 id → ToolNotFoundError；list 只读快照）
execute（size 检查 → 执行 → 错误归一化 → 输出 size 检查；永不抛异常）
```

显式 allowlist：`createToolRegistry()` 只注册选定的 10 个定义；没有文件系统扫描、没有动态
import、没有 plugin magic。生产 allowlist 必须显式。

## 6. 第一批 Tool Allowlist（10 个）

| tool id | 类别 | 说明 |
| --- | --- | --- |
| `json_format` | JSON | 2/4 空格格式化，键序保留 |
| `json_minify` | JSON | 单行压缩 |
| `json_validate` | JSON | 返回 `{ valid, error?, metadata? }`，非法 JSON 是成功输出而非工具错误 |
| `base64_encode` | encoding | UTF-8 文本 → Base64 |
| `base64_decode` | encoding | Base64 → UTF-8 文本 |
| `url_encode` | encoding | `encodeURIComponent` 语义 |
| `url_decode` | encoding | `decodeURIComponent` 语义 |
| `text_count` | text | Unicode 感知字符/中文/单词/行/段落/UTF-8 字节 |
| `text_deduplicate` | text | trim + 去空行 + NFKC 去重（保留首次、保持顺序） |
| `timestamp_convert` | time | 秒/毫秒自动识别 → UTC ISO-8601 |

统一约束：`side_effect = none`、`network_access = false`、`deterministic = true`、全同步、
无 DOM、无 crypto 随机、无文件、无网络、无浏览器存储。

### 确定性裁剪（避免 locale/机器依赖）

- JSON 不开 `sortKeys`（避免 `localeCompare`），键序保留；
- `text_deduplicate` 固定 `caseSensitive=true` + NFKC + `order=keep`，不使用 `Intl.Collator`
  排序、不用随机顺序；
- `timestamp_convert` 固定 `timeZone=UTC`，裁剪 `local` / `relative`（依赖机器时区/当前时间），
  只保留 `iso` / `utc` / `seconds` / `milliseconds`；
- Base64 / URL 只做 UTF-8 文本编解码，二进制文件字节不在 v1 范围。

## 7. 不入选 / 暂不入选

| 类别 | tool | 原因 |
| --- | --- | --- |
| DEFER | `sha256_text` | `crypto.subtle`（async），首版保持全同步 |
| DEFER | `uuid_v4` | 非确定（`deterministic=false`），会复杂化统一契约 |
| DEFER | `jwt_decode` | `exp` 相对当前时间，语义需额外说明 |
| DEFER | `text_sort` | `Intl.Collator` 排序 locale 相关 |
| DEFER | `token_estimate` | 纯启发式，需明确 estimate 语义 |
| DEFER | `doi_*` | 依赖手工 metadata |
| REJECT(13A) | `regex_*` | JS RegExp 无可靠超时，ReDoS 风险 |
| REJECT | image / QR / chart / markdown / design | Canvas / DOM / UI 渲染 |

## 8. Resource Limits

```text
maxInputBytes  = 256 KiB
maxOutputBytes = 256 KiB
timeoutMs      = 2000（reserved；13B 在 server 进程边界执行）
```

- Regex 不在 v1（ReDoS）；
- Hash 优先 text hash 而非任意大文件（且 v1 未入选）；
- Base64 限制编解码大小；
- JSON 限制输入长度，v1 不处理数十 MB payload；
- `timeoutMs` 是预留元数据：13A 工具都是有界同步纯函数，真正的超时抢占由 13B 的 MCP server
  进程级完成。

## 9. Error Model

`execute` 永不抛异常，统一返回 `ToolResult`：

```text
INVALID_INPUT / INPUT_TOO_LARGE / OUTPUT_TOO_LARGE / UNSUPPORTED_OPTION /
EXECUTION_TIMEOUT / TOOL_NOT_FOUND / TOOL_DISABLED / INTERNAL_TOOL_ERROR
```

错误只携带稳定 code + 安全 message + 可选安全 details；**不含** stack trace、绝对路径或原始
异常。JSON 解析错误可以带安全的 line / column / message，但不得回显敏感输入或泄露内部信息。

## 10. Tool Result ≠ Evidence（关键原则）

```text
Evidence
├── PersonalEvidence（Phase 8）
└── WebEvidence（Phase 12）

Tool Observation
└── ToolResult（Phase 13）
```

禁止 `ToolResult → EvidenceItem` 自动转换。确定性计算结果（如 `SHA256('abc')`）来自 tool
contract，不是网页 Evidence，不需要伪造 citation。Phase 14 才建立统一的 `AgentObservation`，
本阶段不得提前实现。

## 11. Phase 13B — MCP Server Runtime（只设计，未实现）

```text
zglab-tools ToolRegistry
        ↓
MCP Server
        ↓ stdio
```

范围：`tools/list` / `tools/call`、schema exposure、timeouts、output bounds、safe errors、
process lifecycle。**transport 冻结为 stdio**：无需公网端口、不增加 Nginx、不需要 MCP 独立
公网 Auth、process boundary 简单、`zglab-rag` 可作为 host 启动/连接。不实现公网
`https://tools.zglab.fun/mcp`。

SDK 选择（13B 再定，以当前真实官方版本为准）：优先 `@modelcontextprotocol/sdk`，需确认稳定
版本、Node 兼容、license 与 stdio transport 支持；13A 不引入该运行时依赖。

## 12. Phase 13C — MCP Client + Capability Integration（只设计，未实现）

```text
zglab-rag
    ↓
MCP Client
    ↓ stdio
zglab-tools MCP Server
```

届时建立 `MCPToolCapability` 或相应 bounded integration；不提前接 `/api/v2/ask`。MCP Tool
Runtime 是与 PersonalKnowledgeSkill / WebResearchSkill 并列的独立边界，Skill 不是 MCP tool
的封装，反之亦然。

## 13. Phase 13D — Security / Evaluation / Production（只设计，未实现）

```text
security / allowlist / timeouts / process supervision / quota / evaluation /
production deployment / rollback
```

## 14. Non-goals（13A 明确不做）

- Phase 14 Agent Planner / LLM tool selection / ReAct loop / multi-step agent；
- tool → Evidence 自动转换；
- public MCP endpoint；
- shell / filesystem write / GitHub write / SSH / deploy / browser automation / arbitrary URL
  fetch / calculator via eval / dynamic code execution / `eval` / `new Function`；
- 无 LLM 调用、无 Search API 调用、无 Web fetch（Tool Runtime 本身是确定性执行基础设施）；
- 不新增 `/api/v2/tool` / `/api/v2/mcp` / `/api/v2/agent`。

## 15. 验收

`zglab-tools` 侧由 `src/tool-core/*.test.ts` 覆盖：valid / invalid input、boundary sizes、
Unicode、empty input、malformed JSON / Base64、deterministic ordering、unknown tool、
duplicate registration、extra fields、安全错误不泄露内部信息。无 LLM judge。
