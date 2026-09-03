# RHA SOTA / production-grade RAG gap analysis (2026-09-03)

范围：基于当前 checkout 的实现与 `docs/repository-evidence.md`，把“代码已有”“本地确定性验收已有”和“生产质量尚无证据”分开。SOTA 对比只引用官方文档、论文或一方源码；没有把第三方营销材料当作证据。

## RHA 已经具备的基础能力

RHA 已经不是空壳：README 明确覆盖 PDF/Word/PPT/Excel/图片解析和 EvidenceUnit 定位（`README.md:9-22`），Go 检索并行执行 BM25、KNN、短语召回，RRF 融合和 Cross-Encoder 降级（`internal/service/search_service.go:186-292`, `:342-367`, `:441-458`），查询链路有 11 个 LangGraph 节点、会话历史和长期记忆（`ai-orchestrator/app/graph.py:392-418`），上传流水线有版本化任务、重试/DLQ、alias 切换（`internal/pipeline/processor.go:85-104`, `pkg/es/alias.go:81-99`），权限过滤在 ES 查询前置（`internal/service/search_service.go:644-658`），并有 trace ID/span 边界（`README.md:60-65`）。

现有证据也很有边界意识：确定性 runtime E2E 覆盖 57 个带定位 EvidenceUnit、权限、引用、DLQ replay 等功能，但明确“不代表生产模型效果或吞吐”（`docs/repository-evidence.md:14-20`）；历史上传摘要是 120/120、merge P95 2444.6 ms，另一个 searchable artifact 为 `searchable_rate=0.0`，都不能推导回答质量（`docs/repository-evidence.md:13`, `benchmarks/README.md:21`）。下面的“证据不足”因此不是对已实现代码的否定。

## 优先级排序的差距

状态定义：**缺失** = 当前没有相应机制；**部分** = 有骨架但能力明显窄于生产/SOTA；**已实现但证据不足** = 代码/契约存在，尚缺真实数据、规模或质量证明。

| 优先级 | 类别 | 当前 RHA 证据（精确路径/行） | 状态 | SOTA 一手对比 | 为什么重要 / 务实下一步 |
|---|---|---|---|---|---|
| P0-0 | 回答可追溯性 | `ai-orchestrator/app/graph.py:279-317,352-371,579-620` 在生成前就从 Top-K 文档组装引用；`ai-orchestrator/app/main.py:156-158` 完成事件直接发送这些引用，没有逐 claim 的蕴含/支持度校验。 | 部分 | RAGAS 将 faithfulness、context precision/recall 等拆成独立指标：[官方文档](https://docs.ragas.io/en/stable/) | “带引用”不等于“引用支持答案”。应把答案拆成 claim，校验每个 claim 与 EvidenceUnit 的支持关系；无充分证据时拒答或二次检索，并评测 citation precision/recall。 |
| P0-1 | 检索质量 | 结构化生产路径把每个 EvidenceUnit 直接变成一个 chunk（`ai-orchestrator/app/evidence_chunking.py:8-26`），没有 parent-child/邻接关系；旧文本路径才使用 1000 字符、100 重叠（`internal/pipeline/processor.go:199-220,964-1004`）。 | 部分 | LlamaIndex Node Parser 支持按语义/标题/句子切分与 metadata 保留：[官方文档](https://docs.llamaindex.ai/en/stable/module_guides/loading/node_parsers/) | 当前结构虽保留来源定位，却缺少自适应粒度和层级检索，长证据可能语义过宽、短证据可能上下文不足。先增加 section parent、邻接窗口和 token-budgeted child chunk，再用同一 qrels 比较。 |
| P0-2 | 检索质量 | `internal/service/search_service.go:354-360` 只有一个 dense `vector` 字段和 `num_candidates=topN*4`；`pkg/es/client.go:215-241` 仅固定维度映射。 | 部分 | ColBERT late-interaction 以 token-level MaxSim 保留细粒度匹配：[ColBERT 一方源码](https://github.com/stanford-futuredata/ColBERT) | 长文、实体和数字查询常被单向量平均掉。增加可选 late-interaction 索引/服务（先离线回放，不改变默认路径），测 Recall@K、nDCG 和延迟。 |
| P0-3 | 检索质量 | `internal/service/search_service.go:251,681-725` 固定 RRF；`ai-orchestrator/app/retrievers.py:93-150` `rrf_k` 为配置常量，未按查询学习权重。 | 部分 | Elasticsearch 官方 RRF 也把融合当作可调 retriever，并提供 rank window/并行子检索：[官方文档](https://www.elastic.co/guide/en/elasticsearch/reference/current/rrf.html) | 不同语言、短查询、代码查询的最佳融合权重不同。记录每路分数/排名，离线校准 weighted-RRF 或 query-class policy，并保留固定 RRF 作为回退。 |
| P0-4 | Agent reasoning | `ai-orchestrator/app/graph.py:142-150,186-223` 最多使用改写查询加 `sub_queries[:2]`，没有检索后再搜索或证据缺口判断。 | 部分 | Corrective RAG 在评估检索质量后触发纠错/重检索：[论文](https://arxiv.org/abs/2401.15884)；Self-RAG 用反思 token 控制检索：[论文](https://arxiv.org/abs/2310.11511) | 一次召回失败会直接进入生成，尤其影响多跳问题。增加“证据充分性/冲突检测 -> query rewrite/retrieve”有限循环，设置最大步数和预算。 |
| P0-5 | Agent reasoning | `ai-orchestrator/app/graph.py:40-51,118-140` 规划器只有 5 类 intent、单一 retrieval_mode；heuristic fallback 依赖固定中文关键词（`:478-510`）。 | 部分 | Haystack 官方 Agent/Pipeline 支持工具调用、条件路由和循环：[官方文档](https://docs.haystack.deepset.ai/docs/pipelines) | 规划器漏掉跨文档、多跳、时间约束和英文变体。定义 typed plan（子问题、证据要求、停止条件），对 planner 输出做 schema/预算校验，并加入多跳测试集。 |
| P0-6 | 检索质量 | 索引 mapping 只有 text/vector/定位字段（`pkg/es/alias.go:58-68`），没有实体关系或社区摘要索引。 | 缺失 | Microsoft GraphRAG 通过实体图、社区检测和全局摘要回答跨文档问题：[一方 GitHub](https://github.com/microsoft/graphrag) | 仅 chunk 相似度难以回答“全局主题/关系/共同原因”。先离线构图并将 entity/community ID 回写 EvidenceUnit，再以 global/local query 两套 qrels 验证成本收益。 |
| P0-7 | 检索质量 | `internal/service/search_service.go:342-367` dense KNN 依赖单一 embedding 服务；没有 learned sparse（SPLADE）或词法扩展。 | 缺失 | SPLADE 一方实现展示稀疏扩展检索：[一方 GitHub](https://github.com/naver/splade) | 专有名词、拼写和低频词会造成 dense 漏召回。先把 sparse 向量作为可选字段并与 BM25 做实验，记录索引体积和查询 P95。 |
| P0-7a | 检索质量 | 知识索引的 `text_content` 仍使用 `standard` analyzer（`pkg/es/client.go:215-256`），虽然 Compose 安装了 IK 插件（`deployments/docker-compose.yaml:138-147`），但 mapping 没有启用中文分词。 | 部分 | Elasticsearch 官方 Text analysis 说明 analyzer 会直接影响全文检索 token 化：[官方文档](https://www.elastic.co/guide/en/elasticsearch/reference/current/analysis.html) | 中文 BM25 可能退化为不符合语义边界的 token。建立中文/英文/代码混合 qrels，对 IK、smartcn 或语言路由 analyzer 做同环境评测，再通过新物理索引和 alias 发布。 |
| P1-8 | 检索质量 | `ai-orchestrator/app/graph.py:253-277` 只是 knowledge/memory 两组 RRF；没有 parent-document、邻居窗口或去重后的上下文扩展。 | 部分 | RAPTOR 递归聚类和摘要建立树状检索：[论文](https://arxiv.org/abs/2401.18059) | 返回孤立 chunk 会丢失上下文，或因 top-K 太小漏掉总览。增加 parent/section retrieval 与邻接窗口，限制 token budget 后比较 answer faithfulness。 |
| P1-9 | 多模态/文档智能 | 图片仅产生 OCR region 和可选 VLM summary（`ai-orchestrator/app/image_ingestion.py:193-251`）；检索仍把 `text_content` 和一个 vector 当主入口（`internal/service/search_service.go:302-365`）。 | 部分 | ColPali 直接对页面视觉 patch 做 late-interaction 检索：[一方 GitHub](https://github.com/illuin-tech/colpali) | 图表、版式和无文字信息无法由 OCR 摘要完整表达。增加 page-image embedding/patch retrieval，引用仍绑定 bbox；先以图表问答集合做 Recall 和定位准确率评估。 |
| P1-10 | 多模态/文档智能 | Excel 解析按 25 行窗口拼接字符串，未保留公式、单元格类型或计算依赖（`ai-orchestrator/app/structured_ingestion.py:216-252`）。 | 部分 | Docling 一方项目强调表格、版式和结构化文档导出：[一方 GitHub](https://github.com/DS4SD/docling) | 财务/运营问题需要列语义、公式和跨行聚合；字符串窗口会产生错误引用。保存 cell coordinates/formulas/schema，提供受限表格执行器并审计每次计算。 |
| P1-11 | 多模态/文档智能 | 生产 PDF 路由要求 MinerU OCR receipt，MinerU 不可用即失败（`ai-orchestrator/app/structured_ingestion.py:83-153`）；无 parser ensemble 或置信度门控。 | 部分 | RAGFlow 一方仓库把多格式解析、版面/表格抽取和可配置 chunking 作为完整 ingestion 能力：[一方 GitHub](https://github.com/infiniflow/ragflow) | 单解析器升级或异常会阻塞整份文档。保留 MinerU 主路由，增加版本化 parser fallback、页级置信度和人工复核队列，避免静默降级。 |
| P1-12 | 多模态/文档智能 | 图片 VLM adapter 明确只返回摘要文本并丢弃尺寸/sha 到 prompt（`ai-orchestrator/app/image_ingestion.py:130-183`）；生成阶段只给 LLM 文本消息（`ai-orchestrator/app/graph.py:319-371`）。 | 部分 | LlamaIndex 多模态接口允许 image/text 同时进入 query/response：[官方文档](https://docs.llamaindex.ai/en/stable/module_guides/models/multi_modal/) | 只能“描述图片”而不能让答案模型核验原图细节。增加受 ACL 保护的 image part、视觉引用和模型能力协商；没有视觉模型时明确降级。 |
| P1-13 | 评测 | 质量指标在在线代码中默认写成 `RecallAt100=-1`、`NDCGAt5=-1`（`internal/service/search_service.go:251-290`）；真实报告依赖外部 qrels（`docs/benchmark-guide.md:5-29`）。 | 已实现但证据不足 | BEIR 提供跨领域零样本检索基准和统一评测协议：[一方 GitHub](https://github.com/beir-cellar/beir) | 当前能重算契约，不代表真实语料质量。建立脱敏企业 qrels、hard-negative 和版本化模型/索引矩阵，至少报告 Recall/MRR/nDCG 的置信区间。 |
| P1-14 | 评测 | `docs/repository-evidence.md:14-20` 明确 runtime 使用确定性模型/OCR/embedding/reranker；`benchmarks/README.md:21` 明确不证明 answer quality。 | 已实现但证据不足 | RAGAS 定义 context precision/recall、faithfulness、answer relevancy 等 RAG 指标：[官方文档](https://docs.ragas.io/en/stable/) | 功能 E2E 通过仍可能幻觉或引用不支持。把检索、引用支持度、答案正确性和拒答率拆开，在真实/合成混合集上由独立 judge 与人工抽样校准。 |
| P1-15 | 评测 | 当前测试主要是 fixture/contract 和 runtime gate（`docs/repository-evidence.md:9-16`），没有对抗、噪声、语言切换或长上下文压力集。 | 缺失 | ARES 用合成数据训练评估器并覆盖 context relevance/answer faithfulness：[一方 GitHub](https://github.com/stanford-futuredata/ARES) | 生产回归可能只在“干净 fixture”上看不出来。增加 adversarial prompt、权限边界、OCR 噪声、跨语言和长文压力测试，按每次模型/索引变更门禁。 |
| P1-16 | 评测/运营 | `internal/service/search_service.go:572-592` 只记录候选数、延迟和 rerank 状态；没有用户反馈、线上实验或 embedding/answer drift。 | 缺失 | Vespa 官方搜索介绍强调 query profiling、ranking profiles 和在线可调排序：[官方文档](https://docs.vespa.ai/en/learn/search-intro.html) | 没有线上质量信号就不能发现召回漂移。记录匿名 query class、点击/采纳/纠错反馈和模型版本，做 shadow/A-B 与漂移告警，禁止记录原文敏感数据。 |
| P1-17 | 服务可靠性 | Go/Python 有本地 trace/span 接口，但 README 只说生产需“补充 SDK/exporter 配置”（`README.md:60-65`）；`ai-orchestrator/app/trace.py:1-80` 主要是 context/log。 | 部分 | OpenTelemetry GenAI 语义约定要求模型、token、operation 等统一属性：[官方规范](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | 现在可串 trace，但难以跨服务比较 token/模型耗时。配置 OTLP exporter、采样和脱敏规则，补齐 gen_ai.operation/name/token usage 指标，并验证 collector 不泄露 prompt。 |
| P1-18 | 服务可靠性 | reranker 有单请求 timeout 后返回融合结果（`internal/service/search_service.go:441-458`），但 embedding、LLM、OCR 没有统一 circuit breaker、bulkhead 或 provider routing。 | 部分 | Haystack Pipeline 文档展示组件级条件分支；Vespa ranking profiles 支持多阶段降级：[Haystack](https://docs.haystack.deepset.ai/docs/pipelines)、[Vespa](https://docs.vespa.ai/en/learn/search-intro.html) | 单个慢模型仍可能耗尽 worker。为每个外部依赖配置并发舱、熔断、重试预算和 fallback（缓存/词法/拒答），做故障注入和 tail-latency SLO。 |
| P1-19 | 服务可靠性/成本 | `ai-orchestrator/app/graph.py:352-370` 对每次请求完整流式调用 answer LLM；`ai-orchestrator/app/memory_tasks.py:130-134` 另发模型请求；没有 token/cost budget 或缓存。 | 缺失 | LlamaIndex 官方 ingestion/query pipeline 支持缓存与批处理组件：[官方文档](https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/) | 多轮记忆会产生隐性双倍模型成本。按租户/请求设置 token、美元和时延预算，缓存 query embedding/稳定检索结果，记录实际 usage 并在超预算时降级。 |
| P1-20 | 安全/治理 | 认证是 JWT、黑名单和单一 shared secret（`internal/middleware/auth.go:14-82`, `internal/middleware/internal_auth.go:12-25`）；ES 过滤仅 `user_id`/`org_tag`/`is_public`（`internal/service/search_service.go:644-658`）。 | 部分 | OWASP LLM Top 10 将 prompt injection、敏感信息泄露、过度代理权限列为核心风险：[官方项目](https://owasp.org/www-project-top-10-for-large-language-model-applications/) | 文档中的恶意指令可能进入 prompt；组织标签也不等于细粒度 ACL。对检索文本做 untrusted-data 标记/注入检测，工具调用采用 allow-list，并把文档 ACL、继承和撤销实时同步到索引。 |
| P1-21 | 安全/治理 | 代码有 token 黑名单和 tracked-secret 扫描（`internal/middleware/auth.go:34-40`, `docs/repository-evidence.md:16`），但没有 PII 分类、租户级保留/删除策略和访问审计事件。 | 缺失 | NIST AI RMF 要求治理、测量、管理和风险追踪贯穿生命周期：[官方框架](https://www.nist.gov/itl/ai-risk-management-framework) | 企业知识库需要证明“谁在何时看到什么、何时删除”。增加数据分类/脱敏、tenant retention jobs、不可抵赖审计日志和导出/删除验证；将审计字段纳入 E2E。 |
| P1-22 | 安全/治理 | `pkg/es/alias.go:81-99` 只原子切换 alias；没有索引加密/密钥轮换、备份恢复演练或跨区域 RPO/RTO 证据。 | 部分 | Elasticsearch 官方安全文档覆盖 TLS、加密、角色和审计；MinIO 官方 SSE 文档覆盖服务端加密：[Elastic security](https://www.elastic.co/guide/en/elasticsearch/reference/current/secure-cluster.html)、[MinIO SSE](https://min.io/docs/minio/linux/administration/server-side-encryption.html) | alias rollback 不是灾备。落实 KMS/TLS/备份加密，定期 restore drill，并记录 RPO/RTO 与恢复后的索引/证据一致性。 |
| P1-25 | 长期记忆 | MySQL 长期记忆创建成功后，embedding 失败会直接返回 `nil`，不建立可检索 ES 文档（`internal/service/memory_service.go:356-390`）；仓库接口只有 create，没有补偿扫描/重建（`internal/repository/memory_repository.go:10-18,95-98`）。 | 部分 | Haystack Pipeline 支持组件级条件路由和循环，可用于持久化后的补偿处理：[官方文档](https://docs.haystack.deepset.ai/docs/pipelines) | 这会形成“MySQL 已记住、检索却永远找不到”的静默分裂。将 memory indexing 也改为 outbox/可重放任务，增加 DB-ES 一致性巡检与删除/更正 API。 |
| P1-26 | 证据产品体验 | WebSocket completion 已带 `citations`，但前端只消费 `completion/error/chunk`（`frontend/src/views/chat/modules/input-box.vue:19-39`）；消息组件仍靠正文里的“来源#”正则下载文件（`frontend/src/views/chat/modules/chat-message.vue:19-50`），未渲染页码、Slide、Sheet 或 bbox。 | 部分 | LlamaIndex 多模态接口允许 image/text 一起进入查询与回答链路：[官方文档](https://docs.llamaindex.ai/en/stable/module_guides/models/multi_modal/) | 后端 provenance 没有变成用户可核验的证据体验。前端应保存结构化 citation，提供原文预览、页/区域高亮、版本标识和无权限/已删除状态。 |
| P1-27 | 数据生命周期 | 删除接口只删除知识索引、chunk/vector、任务和对象（`internal/service/document_service.go:283-318`）；ES helper 只作用于传入的知识索引（`pkg/es/client.go:423-464`），EvidenceRepository 没有删除方法（`internal/repository/evidence_repository.go:10-14`）。 | 部分 | NIST AI RMF 强调全生命周期治理与风险管理：[官方框架](https://www.nist.gov/itl/ai-risk-management-framework) | 当前无法证明 EvidenceUnit、document version、evidence index 和派生记忆被完全删除。实现版本级删除事务/outbox、孤儿清理与删除后不可检索 E2E。 |
| P2-23 | 运维/高可用 | 默认 Compose 中 MySQL、Redis、MinIO、Kafka、Elasticsearch 都是单实例且 `restart: "no"`，Kafka offset/transaction replication factor 为 1（`deployments/docker-compose.yaml:4-17,23-33,41-53,78-103,121-152`）；没有集群编排和容量模型。 | 缺失 | Kubernetes 官方 HPA 根据资源/自定义指标调节副本：[官方文档](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) | 当前“高可用”主要证明任务可恢复，不证明节点/机房故障可用。先定义 RPO/RTO 与 SLO，再提供多副本生产部署、PDB/反亲和、备份恢复和基于 Kafka backlog 的 worker 扩缩容演练。 |
| P2-24 | 运维/成本 | 上传有 file MD5 秒传（`internal/service/upload_service.go:413-423`），但 embedding cache 仅按文档版本、TTL 7200 秒（`internal/pipeline/processor.go:35-39,865-867`）；没有模型/索引变更下的可复用策略。 | 部分 | LlamaIndex ingestion pipeline 官方支持文档 hash 缓存、变更检测和批处理：[官方文档](https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/) | 解析器或 embedding 模型升级会重复计算，成本不可预测。把 parser/model/config hash 纳入缓存键，统计命中率、重复字节和每租户成本，支持可控 warm-up。 |
| P2-28 | 企业数据接入 | 当前主要入口是上传 API，仓库没有 SharePoint、Confluence、OneDrive、Google Drive、S3/Web crawler 等连接器及增量同步状态机。 | 缺失 | Onyx 一方项目提供面向企业知识源的连接器与权限同步实现：[一方 GitHub](https://github.com/onyx-dot-app/onyx) | 手工上传无法维护大规模知识新鲜度和源端撤权。先实现一个高价值连接器，支持增量游标、删除传播、ACL 同步、限流与可重放审计，再抽象 Connector SDK。 |

## 建议的落地顺序

1. 先补 P0-0/P1-13/P1-14/P1-15：把 citation faithfulness、真实 qrels、答案正确性和拒答率做成发布门禁；否则继续优化召回也无法证明有效。
2. 再补 P0-1/P0-2/P0-4/P0-6：结构化/层级 chunk、可选 late-interaction、证据充分性重检索和图检索实验；每项都以同一 qrels 与 token/latency 预算验收。
3. 同步补 P1-17/P1-18/P1-20/P1-21/P1-25/P1-27：OTel GenAI、依赖隔离、prompt-injection/ACL、记忆一致性、审计和完整删除；这些是企业上线前的风险门槛。
4. 再做 P1-9/P1-10/P1-26：视觉/表格 grounding 与可交互证据预览，让多模态 provenance 真正可核验。
5. 最后做 P2-23/P2-24/P2-28 与灾备（P1-22）：把容量、成本、恢复目标和知识源同步变成可观测且可演练的运营契约。

## 一手来源清单（24 个）

1. [LlamaIndex node parsers](https://docs.llamaindex.ai/en/stable/module_guides/loading/node_parsers/)
2. [ColBERT](https://github.com/stanford-futuredata/ColBERT)
3. [Elasticsearch RRF](https://www.elastic.co/guide/en/elasticsearch/reference/current/rrf.html)
4. [Corrective RAG](https://arxiv.org/abs/2401.15884)
5. [Self-RAG](https://arxiv.org/abs/2310.11511)
6. [Haystack pipelines](https://docs.haystack.deepset.ai/docs/pipelines)
7. [Microsoft GraphRAG](https://github.com/microsoft/graphrag)
8. [SPLADE](https://github.com/naver/splade)
9. [RAPTOR](https://arxiv.org/abs/2401.18059)
10. [ColPali](https://github.com/illuin-tech/colpali)
11. [Docling](https://github.com/DS4SD/docling)
12. [RAGFlow](https://github.com/infiniflow/ragflow)
13. [LlamaIndex multimodal](https://docs.llamaindex.ai/en/stable/module_guides/models/multi_modal/)
14. [BEIR](https://github.com/beir-cellar/beir)
15. [RAGAS](https://docs.ragas.io/en/stable/)
16. [ARES](https://github.com/stanford-futuredata/ARES)
17. [Vespa search/ranking](https://docs.vespa.ai/en/learn/search-intro.html)
18. [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
19. [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
20. [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)
21. [Elasticsearch secure cluster](https://www.elastic.co/guide/en/elasticsearch/reference/current/secure-cluster.html)
22. [MinIO server-side encryption](https://min.io/docs/minio/linux/administration/server-side-encryption.html)
23. [Kubernetes HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
24. [LlamaIndex ingestion pipeline](https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/)
25. [Elasticsearch text analysis](https://www.elastic.co/guide/en/elasticsearch/reference/current/analysis.html)
26. [Onyx enterprise connectors](https://github.com/onyx-dot-app/onyx)

**统计：30 项差距，26 个一手来源；按 P0/P1/P2 覆盖检索算法、多模态/文档智能、Agent reasoning、评测、服务可靠性、安全治理、长期记忆、证据体验、数据生命周期、企业数据接入和运维/成本。**
