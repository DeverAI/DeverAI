# done 2026-09-05 交付整治（v8.25）

- [x] 基线：py_compile 182/0 + node --check 16/0 + twins 18C3...B2F6 + _audit_tmp 全绿（gui 51 + server 110）
- [x] Err.log：清零（bridge 懒导入 500 已由 _REPO_ROOT 修复，残留堆栈验证后清空，二次冒烟 0 字节）
- [x] 依赖：requirements.txt + api.txt.example 新建；api_keys.py 路径 parent.parent→parent；.gitignore api_keys.py→api.txt+sync_data/
- [x] 文档：README 安装节 → requirements.txt + api.txt 二选一；Design/Techniques/Fact 与 56+17 模块/8.25 版本对齐
- [x] 存储：webui/lite storage save_json/save_text 统一 newline="" + fsync（对齐 pyqt 存储 CRLF 纪律）
- [x] Git：AGENT.txt/AGENTS.md/README/api_keys.py/sync_server/memory|sessions|terms 等 ?? 统一入库（data/backups/Err.log/sync_data 保持忽略）
- [x] 版本：index.html 17 处 ?v=8.25.0 与 dev_log/20250905.md 一致
- [x] 收口：dev_log/20250905.md + done→updates/20250905.md + todo 空
