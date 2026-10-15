use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RuntimeSource {
    Explicit,
    Path,
    Desktop,
}

impl RuntimeSource {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Explicit => "CODEX_SWITCH_CODEX",
            Self::Path => "PATH",
            Self::Desktop => "desktop application bundle",
        }
    }
}

#[derive(Debug, Clone)]
pub(crate) struct CodexRuntime {
    pub(crate) executable: PathBuf,
    pub(crate) source: RuntimeSource,
}

pub(crate) fn resolve() -> Result<CodexRuntime> {
    if let Some(path) = std::env::var_os("CODEX_SWITCH_CODEX").filter(|value| !value.is_empty()) {
        let executable = absolutize(PathBuf::from(path))?;
        validate_executable(&executable).with_context(|| {
            format!(
                "CODEX_SWITCH_CODEX must name an executable Codex runtime: {}",
                executable.display()
            )
        })?;
        return Ok(CodexRuntime {
            executable,
            source: RuntimeSource::Explicit,
        });
    }

    if let Some(executable) = crate::launch::command_on_path("codex") {
        return Ok(CodexRuntime {
            executable,
            source: RuntimeSource::Path,
        });
    }

    if let Some(executable) = discover_desktop_runtime() {
        return Ok(CodexRuntime {
            executable,
            source: RuntimeSource::Desktop,
        });
    }

    anyhow::bail!(
        "no Codex runtime found. Install the Codex CLI, install the ChatGPT or Codex desktop app, or set CODEX_SWITCH_CODEX to an executable runtime"
    )
}

fn absolutize(path: PathBuf) -> Result<PathBuf> {
    if path.is_absolute() {
        return Ok(path);
    }
    let cwd = std::env::current_dir().context("resolving relative CODEX_SWITCH_CODEX path")?;
    Ok(cwd.join(path))
}

pub(crate) fn discover_desktop_runtime() -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        let roots = dirs::home_dir()
            .map(|home| vec![home.join("Applications"), PathBuf::from("/Applications")])
            .unwrap_or_else(|| vec![PathBuf::from("/Applications")]);
        for root in roots {
            for app in ["ChatGPT.app", "Codex.app"] {
                for relative in [
                    "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
                    "Contents/Resources/codex-cli/bin/codex",
                ] {
                    let candidate = root.join(app).join(relative);
                    if validate_executable(&candidate).is_ok() {
                        return Some(candidate);
                    }
                }
            }
        }
    }
    None
}

fn validate_executable(path: &Path) -> Result<()> {
    let metadata =
        std::fs::metadata(path).with_context(|| format!("checking {}", path.display()))?;
    if !metadata.is_file() {
        anyhow::bail!("{} is not a regular file", path.display());
    }
    #[cfg(unix)]
    if metadata.permissions().mode() & 0o111 == 0 {
        anyhow::bail!("{} is not executable", path.display());
    }
    Ok(())
}

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

#[cfg(test)]
mod tests {
    use super::absolutize;
    use std::path::PathBuf;

    #[test]
    fn relative_override_is_resolved_against_the_current_directory() {
        let path = absolutize(PathBuf::from("runtime/codex")).unwrap();
        assert!(path.is_absolute());
        assert!(path.ends_with("runtime/codex"));
    }
}
