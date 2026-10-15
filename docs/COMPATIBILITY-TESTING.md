# 兼容性验证

本项目把确定性的本地引擎验证放入 CI；真实账号和网关单独验收。当前对齐版本和已运行结果见 [Codex 对齐记录](CODEX-ALIGNMENT.md)。

HTTP、OAuth 回调和引擎回环测试需要监听本机回环端口；执行环境禁止监听时，应先取得测试权限再运行，不能把 `PermissionDenied` 当作产品回归。

## 官方引擎与本地服务

Linux CI 下载官方 Codex `0.162.1`，校验固定 SHA-256，然后分别运行原生和通用模型目录。所有模型响应来自回环地址的固定响应服务，凭据为虚拟值，HOME 和 Codex 状态隔离，不产生模型费用。

本机先构建项目，并把对应平台的官方 Codex 可执行文件传给 `--codex`：

```bash
cargo build --locked --bin codex-switch
python3 scripts/ci/provider-capability-smoke.py \
  --codex /absolute/path/to/codex \
  --switch "$PWD/target/debug/codex-switch" \
  --output /tmp/codex-smoke-native \
  --catalog native --child-model --v2-lifecycle --resume
python3 scripts/ci/provider-capability-smoke.py \
  --codex /absolute/path/to/codex \
  --switch "$PWD/target/debug/codex-switch" \
  --output /tmp/codex-smoke-generic \
  --catalog generic --child-model --resume
```

每次使用新的输出目录。脚本验证 provider 配置和私有请求头、父子模型选择、MCP 工具往返与任务完成；原生目录额外验证 V2 子任务完成、消息接收、后续任务唤醒和运行中中断。恢复检查先再次正常启动，以触发 provider run 清理，再通过非交互 `exec resume` 恢复原始 session，要求线程 ID 完全一致。工具名称出现在 schema 中不等于执行成功。

输出目录包含本地模拟请求、CLI 输出和 `summary.json`，失败退出非零。请求数和子进程运行时间有上限。脚本只用于隔离模拟环境，不接受真实账号凭据；不要把真实会话或 token 放进这些诊断产物。

## 显式真实网关验证

`scripts/provider-live-smoke.py` 验证 Bearer 认证的 Responses 网关：模型调用固定的 `audit_echo` 函数，脚本回传固定结果，再要求模型输出该结果。它不启动 Codex，也不能代替 MCP、多 Agent 或真实账号验证。

默认仅输出计划，不读 API Key、不联网：

```bash
python3 scripts/provider-live-smoke.py --base-url https://gateway.example/v1
```

真实执行必须显式加 `--run`，凭据从环境变量 `CODEX_SWITCH_SMOKE_API_KEY` 读取。可通过终端的安全输入或现有秘密管理工具设置；不要把 key 写进命令参数。根目录 `.env.example` 仅列出变量名，脚本不自动加载 `.env`。

```bash
python3 scripts/provider-live-smoke.py \
  --base-url https://gateway.example/v1 --run
```

- 未指定模型时仅调用一次 `/models`，只接受唯一一个名称含 Luna 的模型；没有或有多个候选时停止，要求显式 `--model <实际模型 ID>`。Luna 是当前测试成本策略，不代表脚本能查询实时价格；显式选择也应使用网关当前最便宜的模型。
- 最多两次 `/responses`，每次 `max_output_tokens=128`，`store=false`；不重试、不跟随重定向。模型调用失败或未完成时立即停止，可能已产生费用。预算包含推理 token，达到上限会报告失败。
- HTTPS 使用默认 TLS 校验；HTTP 只允许显式 `--allow-insecure-localhost` 的回环地址。URL 不接受账号、密码、查询参数或 fragment；不继承代理环境变量。
- 连接/读取超时为 10 秒，响应体最多 1 MiB。输出只包含结果分类和 HTTP 状态，不回显上游错误、响应正文或凭据。

本地契约测试不调用收费模型：

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 -m unittest discover -s scripts/ci -p 'test_*.py'
```

## 真实账号与 daemon 验收

这一项需要专用测试账号以及实际安装的 Codex，不能从本地回环结果推断。为每轮测试创建独立的 `CODEX_HOME` 和 `CODEX_SWITCH_HOME`，整个验收过程保持相同环境；使用 file 凭据存储，并遵守机器上的管理员认证策略。不要复制日常账号的凭据到测试目录。

1. 使用 `codex-switch login <测试别名>` 为两个专用测试账号分别登录，确认账号身份与各自已保存 profile 一致。
2. 明确执行 `codex-switch use <账号 A>`，再启动隔离环境中的 `codex app-server daemon start`。用 `codex app-server daemon version` 确认该环境的 daemon 可访问；版本输出本身不证明账号身份。
3. 执行 `codex-switch use <账号 B>`，确认凭据发生切换，运行中的 daemon 重启成功。重新建立真实客户端连接，确认新会话显示或使用账号 B；只读身份或额度检查即可，若需推理则显式选最便宜的可用模型。
4. 再次选择账号 B，确认未触发多余重启。停止该隔离 daemon 后再切换账号，确认切换不会把已停止的 daemon 启动。
5. 在隔离配置中设置 `[use] restart_app_server = false`，确认切换仍成功且明确提示手动处理 daemon。恢复设置并停止测试 daemon，妥善处理测试凭据。

记录 Codex 版本、操作系统、账号代号、每一步结果及脱敏错误分类，不记录 token、邮箱或原始账户响应。重启失败必须单独报告：它不会回滚已经成功写入的账号凭据。桌面应用和其他操作系统需要各自验收。
