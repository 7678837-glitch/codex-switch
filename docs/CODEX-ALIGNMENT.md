# Codex 对齐与验证范围

## 当前基线（2026-10-15）

- 对齐版本：[Codex 0.162.1](https://github.com/openai/codex/releases/tag/rust-v0.162.1)，commit `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`；最低支持版本仍为 `0.159.2`。
- 已对照 `rust-v0.161.0...rust-v0.162.1` 的认证、令牌存储与刷新、账号额度、模型目录和启动源码。核心认证存储、后端额度结构及 `available_access_programs.cyber` 契约保持一致。
- 0.162.0 的 Provider `capabilities.external_web_access` 与 `capabilities.remote_compaction` 可通过现有 `--set` 传递；省略时由 Codex 和提供方的默认行为决定。原生目录继续保留额外模型元数据。
- 0.162.1 修复 daemon 特性兼容性比较和多行异步问题显示。本项目继续复用 Codex 的启动逻辑，ChatGPT 独立启动保留已有 `--no-daemon` 路由。
- Daybreak 资格从每个账号实时模型目录的 `available_access_programs.cyber` 聚合，Blue 和 Red 独立判断；缺少声明时为未知。资格查询的 `client_version` 固定为已对齐的 `0.162.1`：实测 `0.160.0` 目录会省略访问项目，即使账号具备 Blue。启动模型目录仍遵循本机 CLI 版本。目录资格不代表已经启用 Daybreak，也不能替代推理阶段授权。
- 订阅有效期来自账号路由的 `/backend-api/subscriptions`，实时结果优先；JWT 兜底保留来源与原检查时间。订阅有效期、令牌有效期、配额重置和重置卡到期是不同数据。

### 本地验证（macOS arm64）

- 红灯证据覆盖基线分类、账号元数据契约、实际请求版本、TUI 颜色和窗口布局。桌面新增行为在实现前运行 `cargo test --locked --test test_launch_passthrough <测试名> -- --exact`：`launch_uses_discovered_desktop_engine_when_path_has_no_codex`、`explicit_codex_runtime_override_beats_path_cli` 各 1 项失败；含空格路径的 `desktop_daemon_manual_command_quotes_a_runtime_path_with_spaces` 也先失败。以上测试及桌面重连状态的定向测试已转绿。
- 转绿：发布候选 `20261015.1.0` 上执行 `cargo test --all --locked`，1013 项全部通过（809 项单元、204 项集成），doc-tests 无适用用例。桌面发现、显式引擎优先、无引擎独立运行、手动恢复命令引用及不受 daemon 管理时的重连提示均包含在验证中。
- `cargo fmt --check`、`cargo clippy --all-targets --locked -- -D warnings`、`cargo +1.88.0 check --locked`、`cargo build --locked --bin codex-switch` 和 `git diff --check` 通过；`cargo test --doc --locked` 无适用用例。
- 官方 0.162.1 arm64 引擎压缩包 SHA-256 为 `b293a0fcdd871900a44c0a4f40a1aa401b31e02854f790ebdb8bd5d1177fd1fe`，下载后已校验。
- 原生目录回环验证通过：11 次 Responses 请求，覆盖 V2/Responses Lite、子任务、消息接收、后续唤醒、中断、MCP 和精确会话恢复。
- 通用目录回环验证通过：7 次 Responses 请求，覆盖父子模型路由、MCP、再次启动后的精确会话恢复。两组均使用虚拟凭据、本地固定响应和隔离 HOME，不产生模型费用。
- CI 固定引擎更新为官方 0.162.1 Linux musl 压缩包，SHA-256 为 `86f268d81b898f3e144c5ecff2ad2fda2c5802e042d6195b72538fe0fa125057`。该值来自官方发布资产摘要；本机未运行 Linux/Windows 验证，远端 CI 以发布提交的 GitHub Actions 为准。
- Daybreak Blue 和订阅接口已做真实账号只读验证；Red 依据官方协议和合成目录验证，没有具备 Red 权限的真实账号验收。
- 当前锁文件的 `cargo audit` 通过：`rustls` 升至 `0.23.46`、`quinn-proto` 升至 `0.11.18`，原有 7 条公告已消除。2026-10-15 使用包含 1329 条公告的 RustSec 数据库检查 385 个依赖，漏洞和警告均为零。远端 CI 与发布工作流仍须在实际提交上验证。

### 桌面版与独立运行

运行时按 `CODEX_SWITCH_CODEX`、PATH、已知 macOS 桌面应用安装的顺序选择。显式路径会验证可执行性，相对路径先转为绝对路径；无效显式配置不悄悄换用其他引擎。`launch`、HTTP 客户端版本探测及 managed daemon 操作使用同一解析规则。`--desktop-codex` 只为本次 doctor 额外检查指定文件，不持久化选择。

真实桌面引擎已在空 PATH、隔离 HOME 和虚拟凭据下验证：`doctor` 返回成功，自动选中 ChatGPT 应用内置的 `0.162.0-alpha.17.2`；`launch fixture -- --help` 成功并恢复临时凭据。没有调用模型、更新真实账号文件或重启用户桌面应用。另使用同一内置引擎通过原生目录回环验证：11 次模拟 Responses 请求，覆盖 V2 子任务通信、中断、MCP 往返和精确会话恢复。

引擎不可解析时，隔离环境中的 `use` 仍成功。`doctor` 分别报告 `core_account_commands_available = true` 与 `launch_runtime_available = false`，其非零退出只表示启动引擎未就绪。登录、账号管理、查询和 TUI 不要求安装 CLI 或桌面应用；核心命令可用性不表示已登录或认证策略已经验收。

空 PATH 且引擎不可解析时的额度／订阅／Daybreak 查询和缓存测试通过；同样环境下，TUI 的真实终端启动与信号退出测试通过。本次未重新登录真实账号，登录路径依据原生 OAuth 源码及回环协议测试验证。

当前支持文件型 OAuth 凭据。keyring、auto 和 ephemeral 存储仍被明确拒绝；本次未实现 OS keyring 读写。修改文件或重启 managed daemon 不证明已打开的桌面会话已切号，用户须重新连接并确认账号。Windows／Linux 可显式指定引擎；本机未验收这些平台的桌面安装发现或 GUI 行为。

## 0.161.0 历史对齐记录（2026-10-08）

- 对齐版本：[Codex 0.161.0](https://github.com/openai/codex/releases/tag/rust-v0.161.0)，commit `979011409de0a60b52f179721948e65531d26144`。
- 最低支持版本仍为 `0.159.2`。对齐基线表示已检查的参考实现，不要求用户为本次变更升级最低版本。
- `doctor` 区分当前对齐版本、仍受支持的旧版本、超出基线的新版本和低于最低要求的版本。版本探测与启动能力探测均有 10 秒上限。
- 模型目录、OAuth 文件结构与刷新、额度和 Reset Card、原生 profile、daemon 启动参数和会话恢复是本项目对齐的主要边界。第三方网关的真实推理能力仍取决于服务端。

TUI 优先按所选模型目录声明的推理等级提供选项，保留 `(skip)` 和已有显式选择。没有可用能力信息时，通用预设仅用于手动配置，不代表网关已经支持这些等级。`ultra` 与其他等级一样，不会成为默认选择。

Provider 启动使用本地 `model_catalog_json`。Codex 0.161.0 的 API Key 自动发现和远程目录缓存变化不会自动更新这个快照；普通目录通过 `provider fetch-models <alias>` 更新，显式目录由用户管理。原生模型的额外元数据保留，通用目录不会凭空获得工具或推理能力。

`warmup` 仅为已识别的结构化失败原因提供说明，不展示上游错误消息、原始响应或未知错误码。HTTP 200 仍须等到有效的 `response.completed` 才算预热成功。

本地回环验证使用虚拟凭据和固定响应，不调用收费模型。需要真实模型的验证默认选择当前最便宜的可用模型（目前为 Luna），不得依赖 Codex 自身可能更昂贵的默认模型。真实 ChatGPT 登录与额度、付费网关、运行中的 daemon、桌面端和其他操作系统必须单独记录验证结果，不能从本地回环测试推断。

固定引擎 CI、本机执行方法、显式网关测试的请求与输出上限和真实账号验收步骤见 [兼容性验证](COMPATIBILITY-TESTING.md)。

### 相对 0.159.2 的适配判断

对照两个发布标签的源码与 [0.161.0 发布说明](https://github.com/openai/codex/releases/tag/rust-v0.161.0)，本项目相关变化如下：

| 上游能力或变化 | 本项目处理 |
| --- | --- |
| API Key 模型自动发现默认启用；显式远程目录更严格地处理刷新失败、空目录和重复 slug | 启动仍使用本地 `model_catalog_json`，该路径优先选择静态目录管理器。补充快照及手动刷新诊断，不能声称已获得远程自动刷新。 |
| 原生模型消息新增可选 `content_filter_guidance` | 沿用原生目录未知字段透传，无需新增配置或删减元数据。 |
| Bedrock 的 `ultra`、V2 与 GovCloud 支持 | `ultra` 在 0.159.2 已存在；本次补齐 TUI 按目录呈现等级的缺口。独立 Bedrock 接入按实际需求另行评估。 |
| 默认模型变化、Daybreak opt-in、语音设备及 `/mcp login` 体验 | 由 Codex 自身处理；本项目不新增对应配置界面。真实模型测试显式选最便宜可用模型。 |
| 认证、额度与会话 | `AuthDotJson` 字段、额度解析和核心刷新契约未发现需要适配的变化；`migrate-rollouts` 已在 0.159.2 存在。保留当前启动与恢复行为。 |

### 20261008.1.0 发布前本地验证（macOS arm64）

- 红灯：实现前执行 `cargo test --locked --lib tui::provider -- --test-threads=1`，43 项通过、10 项新增行为失败；版本分类、User-Agent、目录管理说明、SSE 原因和 doctor 的定向测试也分别确认预期失败。
- 转绿：`cargo test --all --locked`，992 项通过（795 项单元测试、197 项集成测试）。补齐表单测试的临时目录隔离后，`cargo test --locked --lib tui::provider` 的 53 项通过。`cargo fmt --check`、`cargo clippy --all-targets --locked -- -D warnings`、`cargo build --locked --bin codex-switch` 和 `git diff --check` 通过。
- 使用官方 Codex 0.161.0 arm64 二进制运行 `scripts/ci/provider-capability-smoke.py`，原生与通用目录、不同子模型、MCP 往返、父任务完成、私有请求头和凭据传递均通过。原生目录覆盖 V2/Responses Lite、消息接收、后续任务唤醒和运行中中断；通用目录覆盖 V1。两者均在再次普通启动后精确恢复原 session，返回 thread ID 与原值一致。测试使用临时 HOME、虚拟凭据和本地固定响应，原生 11 次、通用 7 次 Responses 请求，无收费推理调用。
- 官方二进制的 `doctor --desktop-codex <同一二进制路径>` JSON 检查通过：最低版本 `0.159.2`、对齐版本 `0.161.0`、两项状态均为 `aligned`、`versions_match=true`。指定同一 CLI 文件仅验证版本探测，不代表已验证桌面应用。
- 构建强化：发布注入与显式网关脚本分别有 8 项 Python 契约测试通过，distribution 的 36 项测试通过。真实仓库清单的临时副本通过 `cargo metadata --locked --offline`，注入版本后依赖记录保持不变。CI/release YAML 和 Bash 步骤通过本地语法检查，此处记录本地验证；远端工作流结果以对应提交的 GitHub Actions 为准。
- 测试先行证据：网关入口缺失时 8 项测试失败，完整 SSE 的中间工具调用事件再现了 3 项失败，修复后转绿。发布注入初版未运行实现前红灯；后续合法预发布标识修复有 3 个子用例先失败、8 项测试最终通过的证据。
- V2 证据判定的 10 项 Python 测试通过；实现前 6 项缺少 helper 失败，审查补充的错误工具返回、缺少真实子任务输入和缺少父任务接收证据的 3 项断言先失败后通过。另补独立中断反例，能检测被移除的运行状态校验。远端 CI 检查与单变量实验详见 [消融报告](audits/20261008-ci-ablation.md)。
- 本地验证未覆盖真实 ChatGPT 登录、额度与付费网关、运行中的 daemon、桌面应用，以及 Linux/Windows 上的新变更。

## 0.159.2 历史对齐记录（2026-09-30）

The development candidate is based on project commit `be8ea732d35447a789741aa061aed320d05cb4de`, including the native-profile, daemon deadline, semantic-auth comparison, Windows ACL and dependency fixes already on `dev`.

### Upstream reference

- Released contract: [Codex rust-v0.159.2](https://github.com/openai/codex/releases/tag/rust-v0.159.2), commit `ff6aec96948b70d94983af2641a6b67c94faeff5`.
- Additional source cross-check: [main at 8c3612f](https://github.com/openai/codex/tree/8c3612fb638d356579c446de68421288164484dc), retrieved on 2026-09-30.
- Primary contracts: `codex-rs/protocol/src/openai_models.rs`, `models-manager/src/model_info.rs`, `model-provider/src/models_endpoint.rs`, `model-provider-info/src/lib.rs`, `codex-api/src/endpoint/models.rs`, and `backend-client/src/client/rate_limit_resets.rs`.

The version is an explicit reference point, not a promise that an arbitrary later Codex release has the same private backend contract. The ChatGPT Codex backend and a provider's generic OpenAI-compatible model list have different metadata shapes; a model being unavailable through the public API does not exclude it from a ChatGPT-authenticated catalog.

### Minimum supported executable and desktop engines

The project support minimum and the current Codex alignment baseline are both `0.159.2`. This is a codex-switch support policy, not a claim that older Codex versions cannot work independently. `launch` resolves one PATH executable and checks that exact executable before ChatGPT selection, reset-card consumption, provider run creation, or credential staging; the version probe is bounded to four seconds and uses a temporary `CODEX_HOME`. The existing `--help` capability probe remains separate and still determines ChatGPT `--no-daemon` routing.

`codex-switch doctor` reports the PATH CLI. `doctor --desktop-codex <path>` optionally probes a caller-selected desktop-bundled engine too; it does not scan app bundles or guess paths. A missing/unreadable version or a version below minimum fails the report. Omitting the desktop path is `not_checked`, not a failure. Engines above the alignment baseline but at or above the minimum are `above_baseline_unverified`; if both versions are known, `versions_match` compares core and prerelease versions and ignores build metadata. `version_relation` is `same`, `desktop_engine_newer`, or `desktop_engine_older`, relative to the PATH CLI. A mismatch is informational and does not certify every desktop workflow or daemon integration. Doctor only probes versions: it does not validate auth, system/managed requirements, desktop UI behavior, or daemon compatibility. See OpenAI's [Codex troubleshooting guidance](https://learn.chatgpt.com/docs/reference/troubleshooting) and [Windows app / WSL guidance](https://learn.chatgpt.com/docs/windows/windows-app) for the distinction between app surfaces and execution environments.

The gate, doctor and follow-up audit repairs were verified locally on the Windows host: `cargo test --all --quiet` (906 tests: 724 unit and 182 integration; eight integration binaries contain tests, the Windows TUI-shutdown integration binary has zero applicable tests, and doc-tests contain none), `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo audit` (384 crates checked against 1,277 current RustSec advisories, no advisories found), Git Bash `bash -n scripts/install.sh`, PowerShell parsing of `scripts/install.ps1`, and `git diff --check`.

The official Codex 0.159.2 executable passed `doctor` against the PATH CLI and an explicitly supplied desktop-engine path resolving to the same executable (`aligned`, `versions_match: true`, exit 0). Four isolated JSON cases also passed: a newer 0.160.0 executable is accepted as `above_baseline_unverified`; 0.159.1 fails with exit 1; 0.159.2 with build metadata remains aligned and matches; and JSON output is single-envelope, stdout-only with no stderr diagnostics. These checks used temporary homes and fake executables where applicable, with no real account or production request.

Additional isolated CLI smoke tests confirmed that WIF rejects ChatGPT launch before creating live credentials or provider-run state, with one JSON error and no stderr output; a fake-key provider still launches the official engine's `--help` with WIF and a keyring store present, without creating or swapping `auth.json`. This checks the provider branch and process launch, not a real provider completion. No real account or production backend request was used.

The checks in this section were first run locally on Windows. The prior `20260930.3.0` validation record elsewhere in this document remains a historical result for that candidate and is not replaced by these results. This alignment shipped in stable `v20261001.5.0` (commit `629d3b6`); see [Release outcome](#release-outcome).

### Authentication and audit follow-up

The six actionable findings in the [independent audit](audits/20260930-independent-review.md) are repaired: WIF preflight, managed authentication sources, provider command-auth conflicts, quoted TUI argv, preserved TUI lists after read errors, and reset-credit override routing. ChatGPT policy checks are read-only and run before relevant file-login operations or backend requests; requirements override managed/user defaults. Windows resolves the OS ProgramData known folder, Unix reads `/etc/codex/requirements.toml` and legacy managed defaults, and macOS accepts only forced `com.openai.codex` MDM preferences. Unsupported custom ChatGPT backend routing fails explicitly. This resolver is scoped to authentication constraints; it does not replace Codex's own enforcement of other managed settings.

Refresh remains a special persistence boundary: a received replacement token reaches its saved profile under the existing CAS and refresh identity rules before mutable policy is consulted for live activation. Policy refusal skips activation without discarding that credential; genuine live-auth I/O failures still propagate to the caller. A losing refresh response is not sent or cached; exact credentials already persisted by that same request are accepted idempotently. Strict import and re-login identity checks remain separate.

### Review and repair boundaries

| Boundary | Corrected behavior |
| --- | --- |
| Native model metadata | Preserve upstream instructions, tool support, reasoning levels, modality information and unknown extension fields. |
| Generic model lists | Fill the Codex schema without advertising capabilities that the source did not provide; keep explicit user choices distinct from discovered capability limits. |
| Provider HTTP connection | Resolve the same saved provider overrides used at launch, including explicit model catalogs, authentication headers and routing queries. |
| Responses probes | Treat temporary and ambiguous failures as unknown; scope and expire conclusive results instead of permanently trusting an old boolean. A saved denial is re-checked live at launch before it can refuse. |
| Account authentication | Retry models authentication in a bounded way and use compare-and-swap persistence for rotated credentials. |
| Account routing | Apply account and FedRAMP routing consistently across model, response, usage and reset-credit calls. |
| Session recovery | Scan first JSONL metadata records in active and archived rollouts; retain profiles whenever liveness cannot be established safely, including an unavailable Codex home and linked or damaged rollout metadata. |
| Launch and daemon integration | Preserve Claude's daemon/profile fixes and cover platform-specific compilation, command deadlines and argv parsing. Unknown daemon support stops account staging; failed child-PID persistence stops and reaps the new process while retaining session state. |

### Recent project activity

The review window covers items created from 2026-08-30 through 2026-09-30. GitHub issue bodies, comments, PR descriptions and available changes were cross-checked against the current source; an open dependency PR does not necessarily mean its dependency is still absent from `dev`.

| Items | Assessment in this candidate |
| --- | --- |
| [#99](https://github.com/xjoker/codex-switch/issues/99), [#103](https://github.com/xjoker/codex-switch/pull/103) | Keep the completed-SSE warmup contract and Luna selection. Extend the model/authentication paths without reverting that behavior. Real-account quota activation is distinct from mock-stream completion. |
| [#100](https://github.com/xjoker/codex-switch/issues/100) | Preserve the Windows ACL fast path, semantic auth comparison and bounded optional daemon restart. The remaining intentional restart cost is controlled by `use.restart_app_server`. |
| [#104](https://github.com/xjoker/codex-switch/issues/104), [#105](https://github.com/xjoker/codex-switch/pull/105) | Preserve shared-daemon account-switch handling and embedded launches. Correct platform-specific helper compilation and strengthen command parsing/deadlines. |
| [#98](https://github.com/xjoker/codex-switch/pull/98), [#101](https://github.com/xjoker/codex-switch/pull/101), [#102](https://github.com/xjoker/codex-switch/pull/102) | `dirs` 7.0.0, `toml` 1.1.6 and `clap` 4.6.7 are already present in the baseline lockfile. Their still-open PRs do not require duplicate merges. |
| [#97](https://github.com/xjoker/codex-switch/pull/97) | The intermediate `toml` 1.1.5 proposal is superseded by 1.1.6. |
| [#92](https://github.com/xjoker/codex-switch/pull/92), [#93](https://github.com/xjoker/codex-switch/pull/93), [#94](https://github.com/xjoker/codex-switch/pull/94), [#95](https://github.com/xjoker/codex-switch/pull/95), [#96](https://github.com/xjoker/codex-switch/pull/96) | Release-action, webbrowser, owo-colors, flate2 and thiserror updates are already integrated. Preserve the pinned release action and existing upgrade/provenance gates. |

### CI and release validation

Recent failures span separate causes. The baseline includes the macOS errno portability change and rustls 0.23.45 for RUSTSEC-2026-0285. The latest baseline CI passed Windows and audit but failed Linux/macOS tests; the source still referenced Windows-only test helpers inside `if cfg!(windows)`, which type-checks both branches. This candidate uses compilation attributes instead.

Release eligibility requires the local gate and all three hosts on the exact candidate commit, followed by the six-target Release workflow, legacy upgrade gate and artifact verification described in [RELEASE.md](RELEASE.md). Historical job status alone is not proof that a new candidate is ready to publish.

Independent review covered provider transport, OAuth rotation/routing, native profile cleanup and launch failure paths. Follow-up repairs include path traversal rejection for damaged profile names and removal of endpoint credentials and untrusted authentication response text from diagnostics.

### Candidate validation

Candidate base version: `20260930.3.0` (commit `7a1ec3c`). Local validation was re-run on Windows (rustc/cargo 1.98.0) against that commit, including the `thiserror` 2.0.21 and `rand` 0.10.3 dependency bumps:

- `cargo test --all`: 854 passed (687 unit and 167 integration tests across eight integration binaries), zero failed or ignored; doc-tests contain none. Unix-only process/signal tests still require the Linux/macOS CI jobs.
- `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings`: passed.
- `cargo audit`: passed with no advisories reported, scanning `Cargo.lock` (384 crate dependencies) against 1,277 RustSec advisories. This run covers `thiserror` 2.0.21 and `rand` 0.10.3, the only dependency changes in this candidate.
- Bash and PowerShell installer syntax: passed.
- Official Codex `0.159.2` executable: passed a local HTTP/SSE smoke covering model sync, probe, launch, a second launch that sweeps old runs, and exact-session resume. All five requests used the expected routing, headers and credentials; the model query used `client_version=0.159.2`. The catalog supplied nonempty instructions, resume reused its original native profile, and shared `auth.json`/`config.toml` were not created or replaced.
- The smoke used temporary homes and a mock provider; it did not validate paid-account quota activation against the production ChatGPT backend. It was not repeated for the later `20260930.3.0` changes (launch-time re-probe of a saved Responses denial, warmup retry and refresh recovery, non-ASCII provider headers), which are covered by the unit and integration tests counted above.

Full logs from [baseline CI 36661218187](https://github.com/xjoker/codex-switch/actions/runs/36661218187) confirm that Unix tests incorrectly compiled references to Windows-only helpers. Logs from 36660817223 show that defect together with the macOS `libc::__errno_location` error and RUSTSEC-2026-0285. Runs 36659156361, 36657765597 and 36657253340 confirm the latter two failures. This candidate fixes the test compilation boundary and preserves Claude's portable errno and rustls 0.23.45 repairs. Older PR runs 36018117969, 36014601650 and 36557685054 return `log not found`; their public metadata and associated source changes were reviewed, but their full logs are unavailable.

[First combined CI 36667616283](https://github.com/xjoker/codex-switch/actions/runs/36667616283) passed Linux, Windows and format/audit. Its macOS job exposed a cold-start timeout in the Python test wrapper at the two-second required help probe. The routing probe now has a separate ten-second bound, while the optional version probe remains at two seconds. Regressions cover a three-second valid help response and refusal before any live credential write when help fails. Unknown routing still fails closed.

Remote publication remains gated on all three CI hosts passing for the exact candidate commit. Only then may the rolling `dev` tag move to trigger the six-target release workflow, legacy-upgrade jobs and published-artifact verification. The repository's Actions and rolling release record the remote results; local mock tests do not substitute for those gates.

### Release outcome

The 0.159.2 alignment, the minimum-version gate and the audit repairs were released in stable `v20261001.5.0` on 2026-10-01 from commit `629d3b6`, the same commit the maintainer accepted as `20261001.5.0-dev`. Branch CI passed on Linux, macOS and Windows. The Release workflow built all six archives, attested them, and passed the prepublish upgrade gate from `v0.0.19` and `v20260804.1.0` on all three hosts before publishing. After publication, all six archives matched their SHA-256 files and verified against the build-provenance bundle for that tag and commit. Paid-account quota activation against the production ChatGPT backend and enterprise policy deployments were not exercised by these checks.
