# 审查修复循环索引

每轮固定五阶段：A 并行审查（5 个只读 subagent，文件域不重叠）→ B 甄别复现 → C 检索最优解 → D 修复 → E 验证 → F 文档。

基线（2026-10-27 03:42 实测）：`pytest -q` = **6 failed / 354 passed / 6 skipped**（54.79s）。
6 个失败均为既有环境问题，不是回归：

- `tests/test_auth.py::test_auth_store_hashes_password_and_increments_revision`
- `tests/test_library_upgrade.py::test_library_images_use_bounded_cached_thumbnails`
- `tests/test_media_state.py::test_dimensions_are_probed_in_background_and_refreshed_after_change`
- `tests/test_radio_client.py::test_device_auth_store_only_persists_token_hash`
- `tests/test_thumbnails.py::test_cli_cache_is_reused_by_web_and_isolated_between_sources[False]`
- `tests/test_thumbnails.py::test_cli_cache_is_reused_by_web_and_isolated_between_sources[True]`

任何新增失败都是 P0 阻塞项。

| 轮次 | 日期 | 起点基线 | 发现(P0/P1/P2/P3) | 修复提交 | 文档 |
|------|------|----------|--------------------|----------|------|
| 01 | 2026-10-27 | 6F/354P/6S | 1/4/8/0（确认 24，驳回 5） | `9b9c4ef`，27 文件 `+435/-155` | [round-01](round-01.md) |
| 02 | 2026-10-27 | 2F/358P/6S | 0/4/12/3（确认 17，驳回 2） | 待提交 | [round-02](round-02.md) |

第 01 轮结束后：`pytest -q` = **2 failed / 358 passed / 6 skipped**。剩余 2 个（`test_auth.py:61`、`test_radio_client.py:77`）都是断言 `stat.S_IMODE(...) == 0o600`，Windows 返回 `0o666`，属平台差异，不是产品 bug。

第 02 轮结束后：`pytest -q` = **2 failed / 365 passed / 6 skipped**（74.52s），失败项与第 01 轮相同。

## 目录

- `round-NN.md`：每轮审查报告（发现、复现、处置、检索、修复、验证）
- `backlog.md`：未修问题（按严重度排序，含暂不修理由与前置条件）
- `proposals/`：需要跨模块重构的方案（评审后才动代码）
