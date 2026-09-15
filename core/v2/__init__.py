"""V2 持久层 + 领域服务包。

与 V1（core/db.py）完全隔离：独立数据库 data/data_v2.db，ULID 主键，规范化表。
设计见 docs/v2/step1-data-model.md §10。
"""
