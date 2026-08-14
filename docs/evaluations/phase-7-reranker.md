# Phase 7 Reranker 评测报告

日期：2026-08-14
状态：已完成 CPU 评测；Reranking 保持可选，生产默认仍为 Vector。

## 1. 实验标识

- 数据集：未修改的 `evaluation/retrieval.yaml`，47 条计分 Query 和 3 条 hard negatives
- 数据集 SHA-256：`71d856c3e220e48b3244bd4b7ef8c536bfb8cfc40c9465329e63c442617fd559`
- Embedding profile：`ep_d80ffd5f87de97afb8befe68ac6fb68218bc8c4a6ea7a6cf97f412e6e8979f1b`
- Embedding：`BAAI/bge-small-zh-v1.5`，contextual composition
- 候选来源：仅使用 public-by-default 的 `VectorRetriever`
- Reranker：`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`
- Backend/device：Sentence Transformers CrossEncoder，Torch/CPU
- Passage：`Title + Section + content`
- Batch size：16
- Candidate K：测试 10、20 和 30，主要 baseline 为 20

本实验没有修改任何 Evaluation Query、relevant target、Embedding、passage composition 或
chunking。Reranking 只重新排列原始 Vector Top-N 候选集。

## 2. 模型获取

当前网络环境下，Hugging Face 直连路径没有完成下载，随后通过 Hugging Face 镜像下载完全相同
的指定模型，没有替换模型。

- 本地 snapshot：`runtime/models/mmarco-mMiniLMv2-L12-H384-v1/`
- `model.safetensors`：470,592,698 bytes
- 权重 SHA-256：`5daeca2481a76b5976a2bdc32f0a78532b6716da4f8cd3ff59460ef8d2f359b4`
- 权重传输时间：约 10 分 1 秒
- 完整 snapshot 传输时间：约 11.5 分钟，包含 tokenizer 文件和重试
- 主评测中本地热缓存 Reranker 加载时间：2.70 秒

Snapshot 位于 Git ignored 的 `runtime/`，不得提交。

## 3. 主要结果：Candidate K 20

下表中的 Vector 指标由 Reranker 使用的同一 Top-20 候选集重新计算。其 MRR 为 0.6532，
而不是 Phase 5 完整排名的 0.6542，因为本次比较有意排除了 rank 20 之后的候选。

| 指标 | Vector | Reranked | Reranker - Vector |
|---|---:|---:|---:|
| Recall@1 | 0.5213 | 0.6809 | +0.1596 |
| Recall@3 | 0.6809 | 0.7872 | +0.1064 |
| Recall@5 | 0.7872 | 0.8404 | +0.0532 |
| Recall@10 | 0.8511 | 0.9043 | +0.0532 |
| Recall@20 | 0.9255 | 0.9255 | 0.0000 |
| Recall@30 | 0.9255 | 0.9255 | 0.0000 |
| HitRate@1 | 0.5532 | 0.7234 | +0.1702 |
| HitRate@3 | 0.7021 | 0.8085 | +0.1064 |
| HitRate@5 | 0.8085 | 0.8511 | +0.0426 |
| HitRate@10 | 0.8511 | 0.9149 | +0.0638 |
| HitRate@20 | 0.9362 | 0.9362 | 0.0000 |
| MRR | 0.6532 | 0.7753 | +0.1221 |

`Recall@20` 完全一致，候选集不变量验证通过。

## 4. Candidate K 20 分类指标

每项指标以 `Vector → Reranked` 表示。

| 分类 | Query 数 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| identity | 8 | .625→.750 | .750→.750 | .750→.750 | .750→.750 | .875→.875 | .6780→.7566 |
| knowledge | 16 | .469→.719 | .719→.813 | .813→.938 | .875→.938 | .938→.938 | .6417→.7990 |
| project | 10 | .900→.700 | 1→1 | 1→1 | 1→1 | 1→1 | .9333→.8167 |
| problem | 8 | .250→.750 | .375→.750 | .750→.750 | .875→1 | 1→1 | .4203→.7865 |
| mixed_technical | 5 | .200→.300 | .300→.400 | .400→.500 | .600→.700 | .700→.700 | .4625→.6286 |

Identity、Knowledge、Problem 和 Mixed Query 整体改善。Project Recall@1 和 MRR 退化，
因此汇总指标的提升并不代表分类风险已经消失。

## 5. Candidate K 质量与延迟

| Candidate K | Reranked R@1 | Reranked R@3 | Reranked R@5 | Reranked MRR | Reranker 中位值 | Reranker p95 | Pair 数 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 0.6596 | 0.7660 | 0.7979 | 0.7480 | 898 ms | 1,385 ms | 500 |
| 20 | 0.6809 | 0.7872 | 0.8404 | 0.7753 | 1,737 ms | 2,512 ms | 1,000 |
| 30 | 0.6596 | 0.7872 | 0.8404 | 0.7643 | 2,649 ms | 3,241 ms | 1,500 |

Candidate 20 的 MRR 和 Recall@1 最好。Candidate 10 更快但质量下降；Candidate 30 更慢，
MRR 也低于 20。

主评测的延迟明细：

| 阶段 | 平均值 | 中位值 | p95 | 最大值 |
|---|---:|---:|---:|---:|
| Vector | 40.3 ms | 17.0 ms | 27.3 ms | 951.2 ms |
| Reranker | 1,837.3 ms | 1,736.8 ms | 2,511.8 ms | 3,035.8 ms |
| 总计 | 1,877.9 ms | 1,757.1 ms | 2,532.0 ms | 3,987.2 ms |

Vector 的平均值和最大值包含首次 Query 的异常值；中位值和 p95 更能代表热运行路径。

## 6. 内存

主要 Candidate 20 进程测量结果：

- Reranker 加载前 RSS：428.55 MB
- Reranker 加载后 RSS：862.70 MB
- 观测到的 Reranker 加载增量：434.15 MB
- 进程峰值 RSS：1,486.69 MB

Candidate 10/30 合并运行时，加载前为 428.91 MB，加载后为 864.06 MB，峰值为
1,499.40 MB。该配置可以在 WSL 上运行，但在 2C2G Server 上留给 API、并发请求和操作系统
的空间很少。

## 7. Promotion 与 Demotion

Candidate 20 统计：

- promoted：13
- unchanged：25
- demoted：6
- relevant target 不在候选集：3

输出的 5 个案例：

| Query | 分类 | Relevant 文档 | Vector rank | Rerank rank | 变化 |
|---|---|---|---:|---:|---:|
| problem-04 | problem | `centos7-python312-release-failure-chain` | 16 | 1 | +15 |
| knowledge-16 | knowledge | `durable-h5-audio-chunk-upload` | 15 | 4 | +11 |
| mixed-04 | mixed_technical | `observable-mobile-recording-pipeline` | 16 | 7 | +9 |
| problem-02 | problem | `resume-matches-but-generated-document-empty` | 10 | 1 | +9 |
| identity-02 | identity | `knowledge/identity/profile.md` | 11 | 19 | -8 |

作为对比，Candidate 10 的 promoted/unchanged/demoted/missing 为 10/26/4/7；Candidate 30
为 13/24/7/3。在 Candidate 30 中，`identity-02` 从 rank 11 降到 28。

## 8. Hard Negatives 与分数分布

Candidate 20 relevant score 分布：

- 最小值：-7.9451
- 中位值：5.2173
- 最大值：10.8489

Candidate 20 hard-negative Top1 分布：

- 最小值：-7.8207
- 中位值：-7.7310
- 最大值：-7.0123

| Query | Top1 score | Top2 score | Margin | Top1 结果 |
|---|---:|---:|---:|---|
| hard-negative-01 | -7.7310 | -7.9954 | 0.2644 | `README.md` |
| hard-negative-02 | -7.0123 | -7.4189 | 0.4066 | `resume-matches-but-generated-document-empty` |
| hard-negative-03 | -7.8207 | -7.8944 | 0.0737 | `astro-content-collections-config-driven-content` |

3 条 hard negatives 只用于诊断，不足以设置生产拒答阈值。

## 9. 人工 Query 复核

Reranker Top1 一列记录获胜结果在重排前的原始 Vector rank。没有增加 Query 特殊规则。

| Query | Vector Top1 | Reranker Top1 | 原始→重排 | 结论 |
|---|---|---|---:|---|
| Agent 长期记忆和 Context 有什么区别？ | `常见误区 / Context 越长` (0.8172) | `Memory 和 Context 是两个不同概念` (7.6236) | 4→1 | 更精确章节得到提升 |
| 你的 Agent 长期记忆一般怎么分层？ | `Agent Memory 的分层模型` (0.7548) | 同一章节 (7.3354) | 1→1 | 正确且稳定 |
| 为什么结构化 LLM 调用需要 Provider？ | `为什么需要 Provider 适配层` (0.6819) | 同一章节 (10.0993) | 1→1 | 正确且稳定 |
| Resume Tailor 怎样避免编造经历？ | `Evidence 驱动 / 一句话理解` (0.5070) | `ResumeTailor / 背景与目标` (0.3374) | 4→1 | Astro noise 消失，但精度未改善 |
| 你的 AI 工程技术方向有哪些？ | `profile / AI Engineering` (0.6096) | 同一章节 (4.1392) | 1→1 | 正确且稳定 |
| generation fencing | `不适合只靠 generation` (0.4434) | `Generation Fencing 的核心` (6.5984) | 2→1 | 核心章节得到提升 |
| WebSocket normal close | `问题背景` (0.5335) | `关闭码只描述协议结果` (4.8520) | 2→1 | 更精确章节得到提升 |
| Spring constructor startup failure | 精确 Spring 问题文档 (0.5651) | 泛化的 `README / 问题复盘` (3.8359) | 9→1 | 退化，泛化摘要胜出 |
| CAS unified authentication | `CAS 登录链路` (0.4151) | `需要单独保留的认证边界` (3.1889) | 19→1 | 相关边界章节得到提升 |

7 条 Query 得到改善或保持较强的 Top1。Spring Query 是明确失败案例；Resume Tailor Query
仍然相关，但不能视为精度明显改善。

## 10. 决策

该模型带来了明显的整体质量改善，尤其体现在 Recall@1 和 MRR；Candidate 20 是已测试配置中
质量最好的选择。但暂不把它设为生产默认模式：

- CPU Reranking 中位延迟约 1.74 秒；
- 完整评测进程峰值约 1.49 GB RSS；
- Project 分类和一条人工 Spring Query 出现退化。

生产保持 `vector`。`reranked` 作为显式可选模式保留。后续生产优化任务可以评估 ONNX/INT8
或更充足的资源预算，但不会改变本次结果。

## 11. 复现方式与原始 Artifact

```bash
uv run python -m zglab_rag.evaluation.reranker_compare \
  --candidate-k 20 \
  --reranker-model-path runtime/models/mmarco-mMiniLMv2-L12-H384-v1

uv run python -m zglab_rag.evaluation.reranker_compare \
  --candidate-k 10 --candidate-k 30 \
  --reranker-model-path runtime/models/mmarco-mMiniLMv2-L12-H384-v1
```

本地原始 Artifact：

- `artifacts/evaluation/reranker-compare-20260814T091500827556Z.json` — Candidate 20
- `artifacts/evaluation/reranker-compare-20260814T091838899725Z.json` — Candidate 10 和 30

原始 Artifact 按设计由 Git ignore。本报告是纳入版本控制、用于长期保留的结果摘要。
