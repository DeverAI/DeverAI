# Future.md — 未来需求记录

- 多会话标签（当前为单会话持久化）
- 会话/工作区多端实时协同（v6 已以算力漂移快照推送/冷备续聊实现基础版，后续做实时双向）
- 冷备 Agent 的工具执行能力（当前仅续聊，不执行工具）
- 图标/图片等视觉资产的尺寸改写（对话式增量修改）；入库与多维说明书已在 v6 落地
- 报错镜像生成期拦截加深（当前为系统提示注入，后续可做检索式精准拦截）
- 本地模型通道（Ollama 已天然兼容，后续做模型下拉即用）

## v8.5 网页版对齐工程（用户：网页版功能必须和普通版完全对齐）
- 批次2：模型注册表+三级匹配（char/bm25/api）、审批四模式、资产升级、建议去重、工具裁剪（对齐 desktop/models.py、matcher.py、vault.py）
- 批次3：专家团编排+四层树、toolsmith、notepad、err_mirror
- 批次4：browser/web_search、远程指挥、同步队列、后台任务、补全、命令面板、diff
- 遗留项（批次1 审查记录）：多标签页按 tab_id 隔离 guard/激活轮（当前按 username，多标签同用户会交叉）；轮内回退点三端点（rollback_point/rollback_points/rollback/{round_no}）前端未接线（批次1 commit 已按 guard 保留回退点）；审批挂起时停止按钮无效（Approval.request 与 abortCtrl 未关联，存量）；依赖树扫描对嵌套 node_modules 未排除（dep_tree 为桌面共用模块，改动需双端回归）。
