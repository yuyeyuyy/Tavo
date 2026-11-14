# 旧数据库迁移夹具

phase0.sql / phase1.sql 从 Git 基线 `898328d` 的两个 Drizzle migration 模板字符串逐字节提取，保留起始/结尾换行。manifest.json 记录原位置、版本和 SHA256，测试会验证哈希。

这些文件只用于生成合成旧数据库、验证 Python 导入和桌面历史兼容，不参与当前后端启动或 Alembic 升级。不要修改已执行的旧 SQL 来适配新结构；当前结构通过 backend/migrations 的新 revision 演进。

旧 TS 业务运行时已删除，原代码及学习注释可通过 Git 基线查看；无需为旧库导入保留 Node / Drizzle / SQLite 原生依赖。
