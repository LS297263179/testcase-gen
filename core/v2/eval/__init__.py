"""V2 Step 11 eval 包 - Benchmark 质量评价基础设施（Runtime 外部观测层）。

设计基线：docs/v2/step11-benchmark-evaluation.md（v1.0 冻结）。
边界：不修改 Runtime / orchestrator / core.schemas / DB schema；
本包只读消费 V2 产物 + 文件级 Benchmark 数据（benchmark/{cases,gold,manifests}/）。

当前进度：S2 = schema + loader + 数据集级校验。
S3 起再实现 matching / metrics（本轮按冻结方案明确不提前实现）。
"""
