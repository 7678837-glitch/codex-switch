# 依赖审计（2026-10-15）

本地 `Cargo.lock` 将 RustSec 受影响包替换为 `rustls 0.23.46`（4 条公告的修复下限）和 `quinn-proto 0.11.18`（3 条公告的修复下限）。两项均由 lockfile 中的 `reqwest` 依赖链引入；未做全量更新。

已本地纳入开放 PR：#109 `libc 0.2.190`、#110 `tokio 1.53.2`、#111 `zip 9.0.1`、#114 `serde_json 1.0.154`、#116 `toml 1.1.8+spec-1.1.0`。zip 9 的 `ZipFile::name()` 返回 `Result`，调用点传播带条目序号的错误上下文；固定输出文件的提取路径不变，并有嵌套 Windows 分隔符条目回归测试。

已本地纳入 CI PR：#112 `actions/download-artifact` v8.0.2 固定为 `9000827ccba6bdab643e8b6fd33ac0654aef8333`，#113 `actions/upload-artifact` v7.0.2 固定为 `cf430e030ddbb5b0abf93d22962f4752f3646cd9`，#115 `taiki-e/install-action` v2.87.27 固定为 `ab6895351a88b908e96a4697860d4ffea5e57dbe`。三个 SHA 均已由各上游 GitHub tag ref 确认。现有 Codex 0.162.1 下载 SHA 保留。

MSRV 仍为 Rust 1.88：新增/升级包声明的最低 Rust 版本不超过 1.88（zip 9.0.1 为 1.88；toml 1.1.8 与 quinn-proto 0.11.18 为 1.85）。开放 PR 的旧 CI 状态只作历史记录，不构成当前改动通过证据。

`cargo audit --json` 实际通过：385 个依赖、0 条漏洞、0 条警告；RustSec 数据库包含 1329 条公告，commit `edb58f5974b83eb8302332821cb6d389777aa351`。

以上 8 个 PR 的变更已纳入本地 `dev`，远端 PR 的合并或关闭由维护者另行处理。

`cargo +1.88.0 check --locked`、`cargo clippy --all-targets --locked -- -D warnings` 及实际 ZIP 提取测试均通过；Rust 测试集合的 1013 项验证记录见 [Codex 对齐记录](../CODEX-ALIGNMENT.md)。远端 CI 以发布提交的 GitHub Actions 为准。
