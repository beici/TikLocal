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
| 02 | 2026-10-27 | 2F/358P/6S | 0/4/12/3（确认 17，驳回 2） | `d11b0fc`，29 文件 `+489/-99` | [round-02](round-02.md) |
| 03 | 2026-10-27 | 2F/365P/6S | 0/2/10/13（确认 25，修复 21，记录 4） | `fix: harden thumbnail cache…`，30 文件 `+650/-64` | [round-03](round-03.md) |
| 04 | 2026-10-27 | 2F/381P/6S | 1/3/12/1（确认 17，误报 4，转 backlog 4） | `08fc782`，10 文件 `+184/-18` | [round-04](round-04.md) |
| 05 | 2026-10-27 | 2F/383P/6S | 0/2/10/5（确认 17，全部修复） | `bd72b78`，19 文件 `+666/-75` | [round-05](round-05.md) |
| 06 | 2026-10-27 | 398P/6S/0F | 0/4/7/9（确认 20，修复 15，转 backlog 5） | `fix: localize error surfaces…` | [round-06](round-06.md) |
| 07 | 2026-10-27 | 412P/6S/0F | 0/0/9/7（确认 16，修复 15，转 backlog 1） | `85a94b7`，26 文件 `+759/-38` | [round-07](round-07.md) |

第 01 轮结束后：`pytest -q` = **2 failed / 358 passed / 6 skipped**。剩余 2 个（`test_auth.py:61`、`test_radio_client.py:77`）都是断言 `stat.S_IMODE(...) == 0o600`，Windows 返回 `0o666`，属平台差异，不是产品 bug。

第 02 轮结束后：`pytest -q` = **2 failed / 365 passed / 6 skipped**（74.52s），失败项与第 01 轮相同。

第 03 轮结束后：`pytest -q` = **2 failed / 381 passed / 6 skipped**（新增 16 个用例），失败项不变。

第 04 轮结束后：`pytest -q` = **2 failed / 383 passed / 6 skipped**，失败项不变（本轮修复集中在前端状态机，另新增 2 个认证用例）。

第 05 轮结束后：`pytest -q` = **2 failed / 394 passed / 6 skipped**，失败项不变（新增 13 个回归用例：`tests/test_round05_regressions.py`）。

第 05 轮收尾（2026-10-27）：把那两个 `stat.S_IMODE(...) == 0o600` 断言改为仅在非 Windows 上执行（Windows 的 `os.chmod` 只切换只读位，模式位恒为 `0o666`，属平台差异而非产品缺陷）。当前基线：**398 passed / 6 skipped / 0 failed**，此后任何失败都是回归。

第 06 轮结束后：`pytest -q` = **399 passed / 6 skipped / 0 failed**（75.38s，新增 1 个用例）。

第 07 轮结束后：`pytest -q` = **412 passed / 6 skipped / 0 failed**（79.59s，新增 13 个用例：`test_download_tools.py` 4、`test_json_storage.py` 4、`test_round07_regressions.py` 3、feed/collection 各 1）。

## 目录

- `round-NN.md`：每轮审查报告（发现、复现、处置、检索、修复、验证）
- `backlog.md`：未修问题（按严重度排序，含暂不修理由与前置条件）
- `proposals/`：需要跨模块重构的方案（评审后才动代码）
