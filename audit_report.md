# DeverAI Deep Bug Audit Report

> **处置状态（2026-08-31 v8.19 全量检修轮复核）**：
> - `sync_server.py` 幽灵依赖 → 确认为文档漂移：服务器端部署件刻意不随仓库分发，仓库仅含桌面端同步客户端；Design.md §3/§4.5 已加注记。
> - `sync.py` 快照注入（sync_server_url 未排除）→ 已在早前轮修复（现列于排除集）。
> - `cli_main.py` `/mode` 崩溃 → 已修复（len(parts) < 2 守卫）；过时 `# noqa: F401` 本轮移除。
> - `test_smoke_servers.py` proc.terminate() → 已修复（Popen 移入 try + None 守卫）。
> - `test_desktop_offscreen.py` next() 无默认值 → 已修复（现用 `next(...)` 前置构造守卫）。
> - 本轮新发现并修复：`test_desktop_offscreen.py` main() 悬空 `try:`（IndentationError，整个桌面套件不可运行）；`webui/app/bridge.py` 缺失 `_save_term_envs` 定义（term_create 500）；冒烟测试对 lite 断言 v8.18 终端环境池（lite 刻意不实现，按版本门控）。

## Executive Summary

**Critical finding: `sync_server.py` — the entire sync server referenced throughout all docs — does not exist in the project.** The desktop client (`pyqt/desktop/sync.py`) has full client-side sync code (push/pull/drift/begin/status/finish) but there is no server implementation for it to talk to. This is a ghost dependency referenced in 50+ places.

---

## 1. `tests/test_desktop_offscreen.py` (250 lines, ~37 assertions)

| # | Line | Severity | Issue | Fix |
|---|------|----------|-------|-----|
| 1 | **156** | **Medium** | `next()` without default — crash path. Raises `StopIteration` if only one session exists. If `_new_session()` failed silently, this crashes `test_gui()`. | Add default: `next(..., None)` and guard. |
| 2 | **211-215** | **Low** | Agent thread stop failure swallowed — false-green path. | Log or re-raise after cleanup. |
| 3 | **216** | **Low** | `td.cleanup()` after agent thread stop — can fail on Windows if files held open. | Wrap in try/except. |
| 4 | **70** | **Low** | Race condition in removal loop — check depends on `remove()` semantics. | Verify before/after loop instead. |
| 5 | **126** | **Low** | Tab count vs session count — false positive if "+" button is a tab. | Filter session-only tabs. |

---

## 2. `tests/test_smoke_servers.py` (273 lines, ~44 assertions)

| # | Line | Severity | Issue | Fix |
|---|------|----------|-------|-----|
| 1 | **247** | **High** | `proc.terminate()` when `proc` can be None — `AttributeError` masks real error. | Guard: `if proc is not None:`. |
| 2 | **137** | **Medium** | `r.json()` not protected — crashes on HTML error pages. | Wrap in try/except. |
| 3 | **236** | **Low** | Race condition in audit log check — register may not be visible yet. | Add retry/wait loop. |
| 4 | **260-261** | **Low** | Fixed ports 8791/8792 — conflicts cause confusing failures. | Use OS-assigned ports. |
| 5 | **116-118** | **Low** | Cookie detection fragile — `get_list` version-dependent. | Use `r.cookies` instead. |

---

## 3. `pyqt/desktop/sync.py` (353 lines) — Client-side sync

**CRITICAL: `sync_server.py` does not exist.** The entire server-side implementation is absent from the repo.

| # | Line | Severity | Issue | Fix |
|---|------|----------|-------|-----|
| 1 | **67-69** | **High** | `sync_server_url` NOT excluded from snapshot — config injection via crafted snapshots. | Add to exclusion set. |
| 2 | **148-185** | **Medium** | No payload schema validation — arbitrary config injection. | Validate schema on import. |
| 3 | **210+** | **Medium** | SSRF via `sync_server_url` — can hit internal services. | Block private IPs, enforce https. |
| 4 | **162** | **Low** | Silent fallback when `cryptography` missing — confusing errors. | Raise clear error. |
| 5 | **151-152** | **Low** | JSON envelope detection heuristic is weak. | Stricter structure check. |

---

## 4. `pyqt/cli_main.py` (341 lines)

| # | Line | Severity | Issue | Fix |
|---|------|----------|-------|-----|
| 1 | **290** | **High** | `IndexError` on `/mode` without argument — crashes CLI. | Guard split length. |
| 2 | **200,224** | **Medium** | Empty `call_id` for approval — collisions on multiple pending. | Generate unique fallback ID. |
| 3 | **247** | **Low** | Accesses private `._cancel` — fragile coupling. | Use public cancel method. |
| 4 | **252** | **Low** | Incorrect `# noqa: F401` — import IS used. | Remove noqa comment. |
| 5 | **134** | **Low** | `_C` attribute iteration fragile — silent key exclusion. | Use `getattr(_C, k, "")`. |

---

## Cross-Cutting Findings

| # | Severity | Issue |
|---|----------|-------|
| 1 | **Critical** | `sync_server.py` ghost dependency — referenced 50+ times but missing. |
| 2 | **High** | Snapshot import allows config injection (sync_server_url not excluded). |
| 3 | **Medium** | Smoke test proc.terminate() crashes if Popen fails. |
| 4 | **Medium** | CLI crashes on /mode without argument. |
| 5 | **Low** | Test assertion counts don't match claims (35 vs ~37, 38 vs ~44). |

---

## Recommended Priority Fixes

1. Create `sync_server.py` or remove all references — biggest architectural gap.
2. Add `sync_server_url` to snapshot exclusion in `sync.py:68`.
3. Guard `proc.terminate()` in `test_smoke_servers.py:247`.
4. Guard `/mode` split in `cli_main.py:290`.
5. Add payload schema validation in `import_snapshot_bytes`.
