# Codex 对齐与验证范围

## 当前基线（2026-10-08）

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
