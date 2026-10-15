use std::ffi::OsString;
use std::sync::{Arc, Mutex};

use axum::{
    Json, Router,
    extract::Query,
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    routing::get,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use codex_switch::usage;
use serde_json::json;

static ENV_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

struct Environment(Vec<(&'static str, Option<OsString>)>);

impl Environment {
    fn set(values: &[(&'static str, String)]) -> Self {
        let previous = values
            .iter()
            .map(|(key, value)| {
                let old = std::env::var_os(key);
                // Every test in this executable holds ENV_LOCK.
                unsafe { std::env::set_var(key, value) };
                (*key, old)
            })
            .collect();
        Self(previous)
    }
}

impl Drop for Environment {
    fn drop(&mut self) {
        for (key, previous) in &self.0 {
            unsafe {
                match previous {
                    Some(value) => std::env::set_var(key, value),
                    None => std::env::remove_var(key),
                }
            }
        }
    }
}

async fn check_metadata_refresh(metadata_fails: bool) {
    let _lock = ENV_LOCK.lock().await;
    let seen = Arc::new(Mutex::new(Vec::new()));
    let models_seen = seen.clone();
    let subscription_seen = seen.clone();
    let app = Router::new()
        .route("/usage", get(|| async {
            Json(json!({
                "plan_type": "pro",
                "rate_limit": {"primary_window": {
                    "used_percent": 12, "limit_window_seconds": 18000,
                    "reset_at": 1_900_000_000
                }},
                "rate_limit_reset_credits": {"available_count": 0}
            }))
        }))
        .route("/models", get(move |headers: HeaderMap, Query(query): Query<std::collections::HashMap<String, String>>| async move {
            assert_eq!(headers["authorization"], "Bearer fixture-access");
            assert_eq!(headers["chatgpt-account-id"], "fixture-account");
            assert!(query.get("client_version").is_some_and(|v| !v.is_empty()));
            models_seen.lock().unwrap().push("models");
            if metadata_fails {
                (StatusCode::FORBIDDEN, Json(json!({"error": "secret-echo-must-not-leak"}))).into_response()
            } else {
                Json(json!({"models": [{
                    "slug": "fixture-model",
                    "available_access_programs": {"cyber": ["daybreak_blue", "daybreak_red"]}
                }]})).into_response()
            }
        }))
        .route("/subscriptions", get(move |headers: HeaderMap, Query(query): Query<std::collections::HashMap<String, String>>| async move {
            assert_eq!(headers["authorization"], "Bearer fixture-access");
            assert_eq!(headers["chatgpt-account-id"], "fixture-account");
            assert_eq!(headers["content-type"], "application/json");
            assert_eq!(query.get("account_id").map(String::as_str), Some("fixture-account"));
            subscription_seen.lock().unwrap().push("subscriptions");
            if metadata_fails {
                (StatusCode::TOO_MANY_REQUESTS, Json(json!({"error": "secret-echo-must-not-leak"}))).into_response()
            } else {
                Json(json!({"active_until": "2030-01-01T00:00:00Z"})).into_response()
            }
        }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let home = tempfile::tempdir().unwrap();
    let _env = Environment::set(&[
        ("PATH", String::new()),
        (
            "CODEX_SWITCH_CODEX",
            home.path().join("no-codex-runtime").display().to_string(),
        ),
        (
            "CODEX_HOME",
            home.path().join("codex").display().to_string(),
        ),
        (
            "CODEX_SWITCH_HOME",
            home.path().join("switch").display().to_string(),
        ),
        ("CS_USAGE_URL", format!("http://{address}/usage")),
        ("CS_MODELS_URL", format!("http://{address}/models")),
        (
            "CS_SUBSCRIPTIONS_URL",
            format!("http://{address}/subscriptions"),
        ),
    ]);
    let claims = json!({"https://api.openai.com/auth": {
        "chatgpt_account_id": "fixture-account", "chatgpt_plan_type": "pro",
        "chatgpt_subscription_active_until": "2029-01-01T00:00:00Z",
        "chatgpt_subscription_last_checked": "2028-12-01T00:00:00Z"
    }});
    let auth_path = home.path().join("auth.json");
    std::fs::write(
        &auth_path,
        json!({"tokens": {
            "access_token": "fixture-access",
            "id_token": format!("header.{}.signature", URL_SAFE_NO_PAD.encode(claims.to_string())),
            "account_id": "fixture-account"
        }})
        .to_string(),
    )
    .unwrap();
    let result = usage::fetch_usage_retried_force("fixture", &auth_path, "")
        .await
        .unwrap();
    assert_eq!(result.primary.as_ref().unwrap().used_percent, Some(12.0));
    assert_eq!(
        seen.lock().unwrap().len(),
        2,
        "metadata must not retry or disappear"
    );
    if metadata_fails {
        assert!(result.daybreak.is_none());
        assert_eq!(result.daybreak_error.as_deref(), Some("HTTP 403"));
        assert_eq!(result.subscription_error.as_deref(), Some("HTTP 429"));
        let subscription = result.subscription.as_ref().unwrap();
        assert_eq!(subscription.source, "token");
        assert_eq!(subscription.active_until, "2029-01-01T00:00:00Z");
        assert!(subscription.checked_at.is_some());
        assert!(!format!("{result:?}").contains("secret-echo-must-not-leak"));
    } else {
        let daybreak = result.daybreak.as_ref().unwrap();
        assert!(daybreak.blue && daybreak.red);
        assert!(result.daybreak_error.is_none());
        let subscription = result.subscription.as_ref().unwrap();
        assert_eq!(subscription.source, "live");
        assert_eq!(subscription.active_until, "2030-01-01T00:00:00Z");
        assert!(result.subscription_error.is_none());
    }
    let cached = usage::fetch_usage_retried("fixture", &auth_path, "")
        .await
        .unwrap();
    assert_eq!(cached.daybreak, result.daybreak);
    assert_eq!(cached.subscription, result.subscription);
    assert_eq!(
        seen.lock().unwrap().len(),
        2,
        "a cache hit must not issue metadata requests"
    );
    server.abort();
}

#[tokio::test]
async fn account_refresh_without_a_runtime_routes_live_metadata_and_reuses_its_cache() {
    check_metadata_refresh(false).await;
}

#[tokio::test]
async fn metadata_http_failures_preserve_quota_and_label_token_fallback() {
    check_metadata_refresh(true).await;
}
