# GitHub Actions 与兼容链路消融检查

检查日期：2026-10-08。远端参考提交：`1519f39d8988ae3bf6d55e2379ed05ef9825b03c`；消融对象为包含 0.161.0 适配和 CI 强化的本地工作区。

当前 `dev`、Release 和三个依赖 PR 的 CI 均成功。本地发现并修复了一处发布版本校验不一致，补齐了一处中断证据测试盲点。这份报告记录发版前检查；检查时工作区尚未推送，表中的远端绿色结果不覆盖这些修改。

## Actions 实际结果

抽取最近 20 条运行记录，重点检查当前 `dev` 的 CI/Release、三个开放 PR 的最新 CI，以及最近两次历史失败。下表时长是单次运行观察值，不能用于判断长期性能或波动率。

| 工作流 | 提交 | 结果 | 总时长 | 最慢 job |
| --- | --- | --- | --- | --- |
| [dev CI](https://github.com/xjoker/codex-switch/actions/runs/37110801500) | `1519f39` | 4 个 job 成功 | 3 分 56 秒 | Windows 测试，232 秒 |
| [dev Release](https://github.com/xjoker/codex-switch/actions/runs/37111111325) | `1519f39` | 15 个成功，Homebrew 按 dev 条件跳过 | 6 分 51 秒 | Windows ARM64 构建，309 秒 |
| [PR #110 CI](https://github.com/xjoker/codex-switch/actions/runs/37565231311) | `b6d317f` | 4 个 job 成功 | 3 分 54 秒 | Windows 测试，230 秒 |
| [PR #109 CI](https://github.com/xjoker/codex-switch/actions/runs/37565220179) | `89a7cfb` | 4 个 job 成功 | 4 分 15 秒 | Windows 测试，252 秒 |
| [PR #108 CI](https://github.com/xjoker/codex-switch/actions/runs/37565202805) | `3a59977` | 4 个 job 成功 | 3 分 22 秒 | Windows 测试，198 秒 |

两次历史失败 [36893704975](https://github.com/xjoker/codex-switch/actions/runs/36893704975)、[36889419904](https://github.com/xjoker/codex-switch/actions/runs/36889419904) 都在 `Run Clippy`：`src/usage/scoring.rs:63` 触发 `needless_borrows_for_generic_args`，`-D warnings` 使任务失败。后续参考提交的各平台检查已成功，不是当前仍存在的 CI 红灯。

现有三个系统的测试矩阵、六个平台发布构建、产物校验与 provenance、旧版本升级检查分别验证不同边界，当前证据不足以删除其中任何一项。最慢路径在 Windows，但没有超时或持续失败证据。

## 官方引擎消融

环境为 macOS arm64，官方 Codex `0.161.0`，本地 `target/debug/codex-switch`。每个变体都在临时脚本副本中运行，使用独立 HOME、虚拟凭据和固定回环响应；未修改真实 Codex 配置。六次引擎运行合计 61 次含 `input` 的本地模型请求，不包括目录 GET 和能力探测，不调用收费模型。

基准命令见 [兼容性验证](../COMPATIBILITY-TESTING.md)。原生基准启用 `--child-model --v2-lifecycle --resume`，每个变体只改变下表所述因素。

| 变体 | 模型请求数 | 实际结果 | 能说明什么 |
| --- | ---: | --- | --- |
| 完整原生基准 | 11 | V2 四操作、Lite、MCP、不同子模型、精确恢复全部成功 | 本轮对照成立 |
| 去掉目录的 `multi_agent_version="v2"` | 7 | V2 四项证据失败；MCP 与精确恢复仍成功 | V2 能力取决于原生元数据，不能从普通目录或启动成功推断 |
| 将 `use_responses_lite` 置为 `false` | 11 | V2、MCP、恢复仍成功；Lite 专项断言失败 | Lite 与 V2 是独立的协议覆盖维度，不能用 V2 成功替代 Lite 检查 |
| 不指定 `--child-model` | 11 | 父子均使用 `audit-model`，其他检查通过 | 同模型协作可用，异模型覆盖是独立测试目标 |
| 保留异模型要求，但移除 spawn 参数中的 `model` | 11 | 子任务回落到父模型，路由检查失败；其他链路仍成功 | 必须检查实际请求的模型，启动子任务本身不能证明选模生效 |
| 禁用临时配置中的 MCP server | 10 | `mcp_roundtrip=false`，其他链路通过，整体失败 | MCP 往返检查能检测工具链缺失 |

不同目录或协议的请求数变化代表执行路径变化；这些固定响应实验不能比较真实模型效果、速度或成本。

## 校验约束消融

| 移除或改变的约束 | 结果 | 判断 |
| --- | --- | --- |
| 网关脚本的 `NoRedirect` | 重定向 fixture 收到额外 `/v1/leak` 请求，现有单测失败 | 重定向拒绝有实际作用，保留 |
| 发布注入中的锁文件版本同步 | dev 版本单测失败 | 清单和锁文件必须同时更新本地包版本 |
| 仅改 Cargo.toml，保留 `--locked` | 真实仓库清单副本的 `cargo metadata --offline` 退出 101，锁文件未变 | `--locked` 能阻止不一致的输入进入构建 |
| 在同样不一致的清单上去掉 `--locked` | metadata 退出 0，锁文件被 Cargo 改写 | 成功退出不等于输入未变；不能删除 locked 约束 |
| 同步两个版本并保留 `--locked`（对照） | metadata 退出 0，锁文件保持字节不变 | 版本注入与 locked 可以同时成立 |
| 仅移除 `validate_v2_lifecycle` 的 `previous_status == "running"` | 原有 9 项测试全绿，独立输入可把已完成子任务的中断误判成功 | 原测试存在盲点，已补独立断言 |

原中断反例同时破坏了消息、后续任务和中断状态；前面的失败已经终止状态推进，掩盖了最后一个条件。新增测试保持前三步全部有效，只把 `previous_status` 改为 `completed`。当前实现 10 项通过；同一消融变体变为 10 项中 1 项失败，直接定位中断条件。运行实现无需修改。

## 已修复的发布问题

新版本注入脚本与工作流 tag 校验不一致：工作流接受 `20261003.1.0-0rc1`，注入脚本却退出 2，发布构建会在注入阶段停止。预发布标识中的 `0rc1`、`01beta` 合法，只有纯数字标识禁止前导零，依据 [SemVer 2.0.0 第 9 条](https://semver.org/#spec-item-9)。

已将注入脚本的预发布正则与现有工作流规则对齐。红灯：8 项测试中 3 个合法标识子用例失败。转绿：8 项全部通过，同时确认纯数字 `01`、`rc.01` 仍被拒绝且文件不被改写。根版本格式和不接收 build metadata 的现有发布政策保持不变。

## 后续强化点

- **发布门禁仍依赖人工步骤。** `release.yml` 的发布 job 依赖 meta、build、attest、legacy-upgrade，未自动检查同一 SHA 的 CI 成功；`docs/RELEASE.md` 要求操作者先等待分支 CI。本次仓库 rulesets API 返回空列表。若有权推送标签的人跳过该步骤，工作流本身无法用测试状态阻断发布。建议把同一提交的 CI 成功作为明确的自动门禁；这需要单独修改发布流程，本轮未执行发布或远端规则变更。
- **失败诊断产物尚未上传。** 新 capability smoke 把请求、子命令日志与 summary 写到 `RUNNER_TEMP`，CI 没有对应的失败上传步骤。标准输出仍保留部分错误，但 runner 回收后无法取得完整诊断文件。建议仅在失败时上传该隔离 fixture 的诊断目录并设置短保留期。

## 验证与边界

本轮新增或修改：一个独立中断反例、两个版本标识测试和版本正则修复。发布脚本 8 项、协议证据 10 项通过；网关原有 8 项在对照测试中通过。只重跑受影响集合，未重复全量 Rust 测试。

原始本地记录保存在 `/tmp/codex-switch-ablation-20261008/`：`results.json`、各变体 `summary.json`、`interrupt-mutant-evidence.json`、`strengthened-current.log`、`release-semver-red.log` 和 `release-semver-green.log`。临时变体脚本不进入仓库。

检查覆盖 Actions 和当前 provider/MCP/多 Agent/恢复、发布版本契约；不等同于全库安全或性能审计。未执行新的远端 CI、Linux/Windows 引擎实验、真实账户、付费网关或运行中的 daemon；未提交、推送、合并或发布。
