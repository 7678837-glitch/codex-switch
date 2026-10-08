//! Offline evidence about catalogs and saved agent model choices, not a
//! certification of gateway or tool execution support.

use std::io::Read;

use serde_json::{Value, json};

use super::privacy::redact_url;
use super::*;

fn issue(issues: &mut Vec<Value>, code: &str, error: bool, message: impl Into<String>) {
    issues.push(json!({"code": code, "severity": if error {"error"} else {"warning"}, "message": message.into()}));
}

fn read_catalog(path: &Path) -> Result<Value> {
    let mut bytes = Vec::new();
    File::open(path)?
        .take((MAX_GATEWAY_MODELS_BODY_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MAX_GATEWAY_MODELS_BODY_BYTES {
        anyhow::bail!("catalog is too large");
    }
    Ok(serde_json::from_slice(&bytes)?)
}

/// Returns only selected capability fields; never echo instructions or arbitrary
/// upstream extension data into diagnostics.
fn capabilities(model: &Value) -> Value {
    let mut result = serde_json::Map::new();
    for field in [
        "multi_agent_version",
        "use_responses_lite",
        "apply_patch_tool_type",
        "input_modalities",
        "supported_reasoning_levels",
        "context_window",
    ] {
        result.insert(
            field.into(),
            model.get(field).cloned().unwrap_or(Value::Null),
        );
    }
    Value::Object(result)
}

pub(crate) fn catalog_report(profile: &ProviderProfile) -> Value {
    let mut issues = Vec::new();
    let explicit =
        override_value(&profile.codex_config, "model_catalog_json").map(provider_override_value);
    let catalog_policy = if explicit.is_some() {
        issue(
            &mut issues,
            "catalog_snapshot_policy",
            false,
            "launch uses the local model_catalog_json snapshot; it does not refresh automatically. The explicit catalog file is user-managed.",
        );
        json!({
            "storage": "local_snapshot",
            "automatic_refresh": false,
            "refresh_command": Value::Null,
            "management": "user_explicit",
        })
    } else {
        issue(
            &mut issues,
            "catalog_snapshot_policy",
            false,
            format!(
                "launch uses a local snapshot and does not refresh automatically; run `codex-switch provider fetch-models {}` to refresh the managed catalog.",
                profile.alias
            ),
        );
        json!({
            "storage": "local_snapshot",
            "automatic_refresh": false,
            "refresh_command": format!("codex-switch provider fetch-models {}", profile.alias),
            "management": "codex_switch_manual",
        })
    };
    let path = if let Some(explicit) = &explicit {
        explicit.as_str().map(PathBuf::from)
    } else {
        provider_dir(&profile.alias)
            .ok()
            .map(|dir| dir.join("models.json"))
    };
    let mut source = if explicit.is_some() {
        "explicit"
    } else {
        "generated"
    }
    .to_string();
    let mut catalog = path.as_deref().and_then(|path| read_catalog(path).ok());
    let age_seconds = path
        .as_deref()
        .and_then(|path| std::fs::metadata(path).ok())
        .and_then(|meta| meta.modified().ok())
        .and_then(|time| time.elapsed().ok())
        .map(|age| age.as_secs());
    if catalog
        .as_ref()
        .is_some_and(|catalog| catalog.get("models").and_then(Value::as_array).is_none())
    {
        catalog = None;
    }
    if explicit.is_some() && catalog.is_none() {
        issue(
            &mut issues,
            "catalog_unreadable",
            true,
            "Explicit model catalog is unreadable or invalid; inspect model_catalog_json locally.",
        );
    } else if explicit.is_none() {
        let matches = catalog
            .as_ref()
            .and_then(|catalog| catalog["models"].as_array())
            .is_some_and(|entries| {
                let actual: HashSet<_> = entries
                    .iter()
                    .filter_map(|entry| entry["slug"].as_str())
                    .collect();
                let expected: HashSet<_> = profile
                    .models
                    .iter()
                    .map(|model| model.id.as_str())
                    .collect();
                actual == expected
            });
        if !matches {
            if path.as_ref().is_some_and(|path| path.exists()) {
                issue(
                    &mut issues,
                    "catalog_replaced_at_launch",
                    false,
                    "Saved catalog is invalid or does not match saved models; launch will generate fallback metadata. Fetch models to restore gateway metadata.",
                );
            }
            catalog = Some(build_model_catalog(
                &profile.saved_model_slugs(&profile.default_model),
                &profile.models,
                &[],
                &[],
                &profile.default_model,
                override_context_window(&profile.codex_config),
                profile
                    .resolve_model(None)
                    .ok()
                    .and_then(|model| model.reasoning.as_deref()),
            ));
        } else {
            source = "legacy_unknown".into();
        }
    }
    let catalog = catalog.unwrap_or_else(|| json!({"models": []}));
    let provenance = &catalog["_codex_switch"]["sources"];
    let models: Vec<_> = catalog["models"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|model| {
            let id = model["slug"].as_str()?;
            let model_source = if source == "explicit" {
                "explicit"
            } else {
                provenance
                    .get(id)
                    .and_then(Value::as_str)
                    .unwrap_or(&source)
            };
            Some(json!({"id": id, "source": model_source, "capabilities": capabilities(model)}))
        })
        .collect();
    if source != "explicit" {
        let sources: HashSet<_> = models
            .iter()
            .filter_map(|model| model["source"].as_str())
            .collect();
        if sources.len() == 1 {
            source = sources.into_iter().next().unwrap().into();
        } else if sources.len() > 1 {
            source = "mixed".into();
        }
    }
    if models.iter().any(|model| {
        matches!(
            model["source"].as_str(),
            Some("generated" | "generic_gateway" | "generic_fallback" | "legacy_unknown")
        )
    }) {
        issue(
            &mut issues,
            "metadata_not_native",
            false,
            "Some models use generic or untracked metadata; fetch native gateway metadata before assuming V2, patch, image or Lite support.",
        );
    }
    json!({"source": source, "file_age_seconds": age_seconds, "saved_at": catalog["_codex_switch"]["saved_at"], "catalog_policy": catalog_policy, "models": models, "issues": issues})
}

pub(crate) fn diagnose(profile: &ProviderProfile, requested_children: &[String]) -> Value {
    let catalog = catalog_report(profile);
    let mut issues = catalog["issues"].as_array().cloned().unwrap_or_default();
    let models = catalog["models"].as_array().cloned().unwrap_or_default();
    if !models
        .iter()
        .any(|model| model["id"] == profile.default_model)
    {
        issue(
            &mut issues,
            "default_model_missing",
            true,
            "The provider default model is absent from the launch catalog.",
        );
    }
    let effective_base_url = match resolve_provider_connection(profile) {
        Ok(connection) => Some(redact_url(&connection.base_url)),
        Err(_) => {
            issue(
                &mut issues,
                "connection_config_invalid",
                true,
                "Connection settings cannot be resolved. Check wire_api, URLs, env_key, headers and conflicting authentication locally.",
            );
            None
        }
    };
    if profile.with_private_headers().is_err() {
        issue(
            &mut issues,
            "header_config_invalid",
            true,
            "Header overrides cannot be transported safely; use dotted http_headers or env_http_headers settings.",
        );
    }
    let codex_home = auth::user_codex_home().ok();
    let config_path = codex_home.as_ref().map(|home| home.join("config.toml"));
    let mut config = match config_path
        .as_ref()
        .map(|path| load_toml_if_present(path))
        .transpose()
    {
        Ok(value) => value
            .flatten()
            .and_then(|value| value.as_table().cloned())
            .unwrap_or_default(),
        Err(_) => {
            issue(
                &mut issues,
                "codex_config_unreadable",
                true,
                "CODEX_HOME/config.toml could not be read or parsed; its contents have been omitted.",
            );
            toml::map::Map::new()
        }
    };
    let mut saved_overrides = toml::map::Map::new();
    for entry in &profile.codex_config {
        if let Some((key, raw)) = entry.split_once('=') {
            let _ = insert_toml_override(
                &mut saved_overrides,
                key.trim(),
                provider_override_value(raw.trim()),
            );
        }
        if let Some((key, raw)) = entry.split_once('=')
            && insert_toml_override(&mut config, key.trim(), provider_override_value(raw.trim()))
                .is_err()
        {
            issue(
                &mut issues,
                "override_conflict",
                true,
                "Saved overrides contain a conflicting key path; inspect Extra -c locally.",
            );
        }
    }
    let mut children: Vec<(String, String, Option<String>)> = requested_children
        .iter()
        .map(|id| ("requested".into(), id.clone(), None))
        .collect();
    if let Some(agents) = config.get("agents").and_then(toml::Value::as_table) {
        let default_model = agents
            .get("default_subagent_model")
            .and_then(toml::Value::as_str)
            .unwrap_or(&profile.default_model);
        let default_effort = agents
            .get("default_subagent_reasoning_effort")
            .and_then(toml::Value::as_str);
        if agents.contains_key("default_subagent_model") || default_effort.is_some() {
            children.push((
                "agents.default_subagent_model".into(),
                default_model.into(),
                default_effort.map(str::to_string),
            ));
        }
        for (role, settings) in agents {
            if let Some(file) = settings.get("config_file").and_then(toml::Value::as_str) {
                let path = PathBuf::from(file);
                let path = if path.is_absolute() {
                    path
                } else if saved_overrides
                    .get("agents")
                    .and_then(|agents| agents.get(role))
                    .and_then(|settings| settings.get("config_file"))
                    .is_some()
                {
                    std::env::current_dir().unwrap_or_default().join(path)
                } else {
                    codex_home.clone().unwrap_or_default().join(path)
                };
                match load_toml_if_present(&path) {
                    Ok(Some(role_config)) => {
                        if role_config.get("model_provider").is_some() {
                            issue(
                                &mut issues,
                                "role_provider_override",
                                true,
                                format!(
                                    "Agent role {role} sets model_provider; provider routing through custom roles is not supported by the aligned Codex version."
                                ),
                            );
                        }
                        let model = role_config
                            .get("model")
                            .and_then(toml::Value::as_str)
                            .unwrap_or(default_model);
                        children.push((
                            format!("agents.{role}"),
                            model.into(),
                            role_config
                                .get("model_reasoning_effort")
                                .and_then(toml::Value::as_str)
                                .or(default_effort)
                                .map(str::to_string),
                        ));
                    }
                    _ => issue(
                        &mut issues,
                        "agent_config_unreadable",
                        true,
                        format!(
                            "Agent role {role} config_file could not be read or parsed; contents omitted."
                        ),
                    ),
                }
            }
        }
    }
    if children.is_empty() {
        children.push((
            "inherited_default".into(),
            profile.default_model.clone(),
            None,
        ));
    }
    let child_models: Vec<_> = children.into_iter().map(|(origin, id, effort)| {
        let model = models.iter().find(|model| model["id"] == id);
        if model.is_none() {
            issue(&mut issues, "child_model_missing", true, format!("Child model {id} ({origin}) is absent from the launch catalog; save/fetch it or change the agent model."));
        }
        if let (Some(model), Some(effort)) = (model, effort.as_deref()) {
            let supported = model["capabilities"]["supported_reasoning_levels"].as_array().is_some_and(|levels| levels.iter().any(|level| level["effort"].as_str() == Some(effort)));
            if !supported { issue(&mut issues, "child_reasoning_unadvertised", false, format!("Child model {id} does not advertise reasoning effort {effort}; Codex may reject the override.")); }
        }
        json!({"origin": origin, "model": id, "available": model.is_some(), "reasoning": effort})
    }).collect();
    let ok = !issues.iter().any(|issue| issue["severity"] == "error");
    json!({
        "ok": ok, "alias": profile.alias, "network_checked": false,
        "scope": "Saved provider and CODEX_HOME/config.toml plus referenced roles; project/system policy, launch CLI and resumed profiles are not evaluated.",
        "capability_evidence": "Catalog metadata only; tools, SSE, gateway translation and model reachability are not tested.",
        "effective_base_url": effective_base_url,
        "catalog": {"source": catalog["source"], "file_age_seconds": catalog["file_age_seconds"], "saved_at": catalog["saved_at"], "catalog_policy": catalog["catalog_policy"]},
        "models": models, "child_models": child_models, "issues": issues
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_report_describes_manual_local_snapshot_and_explicit_file_management() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("models.json");
        std::fs::write(
            &path,
            r#"{"models":[{"slug":"demo/model"}],"_codex_switch":{"saved_at":"2026-10-08T00:00:00Z"}}"#,
        )
        .unwrap();
        let mut profile = ProviderProfile::build(
            "demo",
            "https://example.test/v1",
            vec![ProviderModel::from_id("demo/model")],
            "sk-test",
        );
        profile.codex_config = vec![format!("model_catalog_json={}", path.display())];

        let report = catalog_report(&profile);
        assert_eq!(report["source"], "explicit");
        assert_eq!(report["catalog_policy"]["storage"], "local_snapshot");
        assert_eq!(report["catalog_policy"]["automatic_refresh"], false);
        assert_eq!(report["catalog_policy"]["refresh_command"], Value::Null);
        assert_eq!(report["catalog_policy"]["management"], "user_explicit");
        let issue = report["issues"]
            .as_array()
            .unwrap()
            .iter()
            .find(|issue| {
                issue["message"]
                    .as_str()
                    .is_some_and(|message| message.contains("model_catalog_json"))
            })
            .expect("diagnose must expose local catalog management in its existing issue output");
        assert_eq!(issue["severity"], "warning");
        let message = issue["message"].as_str().unwrap();
        assert!(message.contains("user-managed"), "{message}");
        assert!(
            message.contains("does not refresh automatically"),
            "{message}"
        );
        assert!(!message.contains("provider fetch-models"), "{message}");
    }

    #[test]
    fn catalog_report_points_managed_cache_to_manual_fetch_models() {
        let _lock = crate::profile::TEST_ENV_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let temp = tempfile::tempdir().unwrap();
        let previous = std::env::var_os("CODEX_SWITCH_HOME");
        unsafe {
            std::env::set_var("CODEX_SWITCH_HOME", temp.path());
        }
        let provider_dir = temp.path().join("providers/managed");
        std::fs::create_dir_all(&provider_dir).unwrap();
        std::fs::write(
            provider_dir.join("models.json"),
            r#"{"models":[{"slug":"managed/model"}],"_codex_switch":{"saved_at":"2026-10-08T00:00:00Z"}}"#,
        )
        .unwrap();
        let profile = ProviderProfile::build(
            "managed",
            "https://example.test/v1",
            vec![ProviderModel::from_id("managed/model")],
            "sk-test",
        );

        let report = catalog_report(&profile);
        match previous {
            Some(value) => unsafe { std::env::set_var("CODEX_SWITCH_HOME", value) },
            None => unsafe { std::env::remove_var("CODEX_SWITCH_HOME") },
        }
        assert_eq!(report["catalog_policy"]["storage"], "local_snapshot");
        assert_eq!(report["catalog_policy"]["automatic_refresh"], false);
        assert_eq!(
            report["catalog_policy"]["refresh_command"],
            "codex-switch provider fetch-models managed"
        );
        assert_eq!(
            report["catalog_policy"]["management"],
            "codex_switch_manual"
        );
        let issue = report["issues"]
            .as_array()
            .unwrap()
            .iter()
            .find(|issue| {
                issue["message"]
                    .as_str()
                    .is_some_and(|message| message.contains("provider fetch-models managed"))
            })
            .expect("diagnose must expose manual refresh for the managed cache");
        assert_eq!(issue["severity"], "warning");
        let message = issue["message"].as_str().unwrap();
        assert!(message.contains("local snapshot"), "{message}");
        assert!(
            message.contains("does not refresh automatically"),
            "{message}"
        );
    }
}
