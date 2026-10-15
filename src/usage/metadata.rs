use std::time::Duration;

use serde_json::Value;

use super::{DaybreakAccess, SubscriptionInfo, UsageInfo};

const MODELS_URL: &str = "https://chatgpt.com/backend-api/codex/models";
const SUBSCRIPTIONS_URL: &str = "https://chatgpt.com/backend-api/subscriptions";
const METADATA_TIMEOUT: Duration = Duration::from_secs(5);

fn models_url() -> String {
    std::env::var("CS_MODELS_URL").unwrap_or_else(|_| MODELS_URL.to_string())
}

fn subscriptions_url() -> String {
    std::env::var("CS_SUBSCRIPTIONS_URL").unwrap_or_else(|_| SUBSCRIPTIONS_URL.to_string())
}

/// Tests redirecting the quota endpoint must opt into metadata endpoints too.
/// This prevents an isolated loopback usage test from making a real request.
fn metadata_endpoint_enabled(override_name: &str) -> bool {
    std::env::var_os("CS_USAGE_URL").is_none() || std::env::var_os(override_name).is_some()
}

pub(super) fn parse_daybreak_catalogue(body: &Value) -> Option<DaybreakAccess> {
    let models = body.get("models")?.as_array()?;
    if models.is_empty() {
        return None;
    }
    let mut valid_models = 0;
    let mut incomplete = false;
    let mut blue = false;
    let mut red = false;
    for model in models {
        if model
            .get("slug")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
        {
            continue;
        }
        valid_models += 1;
        let Some(programs) = model
            .pointer("/available_access_programs/cyber")
            .and_then(Value::as_array)
        else {
            incomplete = true;
            continue;
        };
        for program in programs {
            let Some(program) = program.as_str() else {
                incomplete = true;
                continue;
            };
            blue |= program == "daybreak_blue";
            red |= program == "daybreak_red";
        }
    }
    if valid_models == 0 || (incomplete && !blue && !red) {
        None
    } else {
        Some(DaybreakAccess { blue, red })
    }
}

fn catalogue_is_partial(body: &Value) -> bool {
    body.get("models")
        .and_then(Value::as_array)
        .is_some_and(|models| {
            models.iter().any(|model| {
                let valid_model = model
                    .get("slug")
                    .and_then(Value::as_str)
                    .is_some_and(|slug| !slug.is_empty());
                let programs = model
                    .pointer("/available_access_programs/cyber")
                    .and_then(Value::as_array);
                valid_model
                    && programs
                        .is_none_or(|programs| programs.iter().any(|value| !value.is_string()))
            })
        })
}

pub(super) fn subscription_from_live_or_token(
    live_active_until: Option<&str>,
    fallback: Option<SubscriptionInfo>,
    checked_at: i64,
) -> Option<SubscriptionInfo> {
    live_active_until
        .filter(|value| chrono::DateTime::parse_from_rfc3339(value).is_ok())
        .map(|active_until| SubscriptionInfo {
            active_until: active_until.to_string(),
            source: "live".to_string(),
            checked_at: Some(checked_at),
        })
        .or(fallback)
}

fn token_subscription(id_token: Option<&str>) -> Option<SubscriptionInfo> {
    crate::jwt::subscription_snapshot_from_id_token(id_token).map(|(active_until, checked_at)| {
        SubscriptionInfo {
            active_until,
            source: "token".to_string(),
            checked_at,
        }
    })
}

fn metadata_error(status: Option<reqwest::StatusCode>) -> String {
    status.map_or_else(
        || "transport_error".to_string(),
        |status| format!("HTTP {}", status.as_u16()),
    )
}

async fn fetch_json(request: reqwest::RequestBuilder) -> Result<Value, String> {
    tokio::time::timeout(METADATA_TIMEOUT, async {
        let response = request
            .send()
            .await
            .map_err(|_| "transport_error".to_string())?;
        if !response.status().is_success() {
            return Err(metadata_error(Some(response.status())));
        }
        response
            .json()
            .await
            .map_err(|_| "invalid_response".to_string())
    })
    .await
    .map_err(|_| "timeout".to_string())?
}

fn build_models_request(
    client: &reqwest::Client,
    access_token: &str,
    account_id: &str,
    is_fedramp: bool,
) -> reqwest::RequestBuilder {
    // 旧客户端目录会省略访问项目；资格查询固定使用已对齐协议。
    crate::usage::apply_account_routing_headers(
        client
            .get(models_url())
            .query(&[("client_version", crate::auth::ALIGNED_CODEX_VERSION)])
            .bearer_auth(access_token),
        Some(account_id),
        is_fedramp,
    )
}

fn build_subscriptions_request(
    client: &reqwest::Client,
    access_token: &str,
    account_id: &str,
    is_fedramp: bool,
) -> reqwest::RequestBuilder {
    crate::usage::apply_account_routing_headers(
        client
            .get(subscriptions_url())
            .query(&[("account_id", account_id)])
            .bearer_auth(access_token)
            .header("Content-Type", "application/json"),
        Some(account_id),
        is_fedramp,
    )
}

fn apply_metadata_results(
    usage: &mut UsageInfo,
    models: Option<Result<Value, String>>,
    subscriptions: Option<Result<Value, String>>,
    fallback: Option<SubscriptionInfo>,
    checked_at: i64,
) {
    match models {
        None => {}
        Some(Ok(body)) => {
            usage.daybreak = parse_daybreak_catalogue(&body);
            if usage.daybreak.is_none() {
                usage.daybreak_error = Some("invalid_catalog".to_string());
            } else if catalogue_is_partial(&body) {
                usage.daybreak_error = Some("partial_catalog".to_string());
            }
        }
        Some(Err(error)) => usage.daybreak_error = Some(error),
    }
    let live = subscriptions
        .as_ref()
        .and_then(|result| result.as_ref().ok())
        .and_then(|body| body.get("active_until"))
        .and_then(Value::as_str);
    usage.subscription = subscription_from_live_or_token(live, fallback, checked_at);
    if let Some(subscriptions) = subscriptions
        && usage
            .subscription
            .as_ref()
            .is_none_or(|info| info.source != "live")
    {
        usage.subscription_error = Some(match subscriptions {
            Ok(_) => "invalid_response".to_string(),
            Err(error) => error,
        });
    }
}

/// Best-effort metadata enrichment. It deliberately has no retries or OAuth
/// refresh path, so a metadata problem cannot alter quota availability.
pub(super) async fn enrich_usage_metadata(
    usage: &mut UsageInfo,
    access_token: &str,
    id_token: Option<&str>,
    account_id: Option<&str>,
    is_fedramp: bool,
) {
    let fallback = token_subscription(id_token);
    let models_enabled = metadata_endpoint_enabled("CS_MODELS_URL");
    let subscriptions_enabled = metadata_endpoint_enabled("CS_SUBSCRIPTIONS_URL");
    if !models_enabled && !subscriptions_enabled {
        usage.subscription = fallback;
        return;
    }
    let Some(account_id) = account_id.filter(|value| !value.trim().is_empty()) else {
        usage.subscription = fallback;
        usage.daybreak_error = Some("missing_account_id".to_string());
        usage.subscription_error = Some("missing_account_id".to_string());
        return;
    };
    let Ok(client) =
        crate::auth::build_http_client_with_redirect_policy(reqwest::redirect::Policy::none())
    else {
        usage.subscription = fallback;
        usage.daybreak_error = Some("client_error".to_string());
        usage.subscription_error = Some("client_error".to_string());
        return;
    };
    let models_request =
        models_enabled.then(|| build_models_request(&client, access_token, account_id, is_fedramp));
    let subscriptions_request = subscriptions_enabled
        .then(|| build_subscriptions_request(&client, access_token, account_id, is_fedramp));
    let (models, subscriptions) = tokio::join!(
        async {
            match models_request {
                Some(request) => Some(fetch_json(request).await),
                None => None,
            }
        },
        async {
            match subscriptions_request {
                Some(request) => Some(fetch_json(request).await),
                None => None,
            }
        }
    );
    apply_metadata_results(
        usage,
        models,
        subscriptions,
        fallback,
        crate::auth::now_unix_secs(),
    );
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn daybreak_catalogue_detects_blue_red_and_both() {
        let blue = json!({"models": [{"slug": "gpt-5.4", "available_access_programs": {"cyber": ["daybreak_blue"]}}]});
        let red = json!({"models": [{"slug": "gpt-5.4", "available_access_programs": {"cyber": ["daybreak_red"]}}]});
        let both = json!({"models": [
            {"slug": "gpt-5.4", "available_access_programs": {"cyber": ["daybreak_blue"]}},
            {"slug": "gpt-5.4-codex", "available_access_programs": {"cyber": ["daybreak_red"]}}
        ]});

        assert_eq!(
            parse_daybreak_catalogue(&blue),
            Some(DaybreakAccess {
                blue: true,
                red: false
            })
        );
        assert_eq!(
            parse_daybreak_catalogue(&red),
            Some(DaybreakAccess {
                blue: false,
                red: true
            })
        );
        assert_eq!(
            parse_daybreak_catalogue(&both),
            Some(DaybreakAccess {
                blue: true,
                red: true
            })
        );
    }

    #[test]
    fn daybreak_catalogue_treats_missing_or_empty_catalogue_as_unknown() {
        assert_eq!(parse_daybreak_catalogue(&json!({})), None);
        assert_eq!(parse_daybreak_catalogue(&json!({"models": []})), None);
        assert_eq!(
            parse_daybreak_catalogue(&json!({"models": [{"slug": "gpt-daybreak-blue-latest"}]})),
            None
        );
    }

    #[test]
    fn daybreak_catalogue_accepts_explicit_standard_only_as_known_absent() {
        let catalogue = json!({"models": [{"slug": "gpt-5.4", "available_access_programs": {"cyber": ["standard"]}}]});
        assert_eq!(
            parse_daybreak_catalogue(&catalogue),
            Some(DaybreakAccess {
                blue: false,
                red: false
            })
        );
    }

    #[test]
    fn subscription_prefers_valid_live_value_and_falls_back_to_token_snapshot() {
        let fallback = SubscriptionInfo {
            active_until: "2026-11-14T10:40:00Z".into(),
            source: "token".into(),
            checked_at: Some(123),
        };
        assert_eq!(
            subscription_from_live_or_token(
                Some("2026-12-01T00:00:00Z"),
                Some(fallback.clone()),
                456
            ),
            Some(SubscriptionInfo {
                active_until: "2026-12-01T00:00:00Z".into(),
                source: "live".into(),
                checked_at: Some(456),
            })
        );
        assert_eq!(
            subscription_from_live_or_token(Some("not-a-date"), Some(fallback.clone()), 456),
            Some(fallback)
        );
        assert_eq!(subscription_from_live_or_token(None, None, 456), None);
    }

    #[test]
    fn metadata_http_failures_are_classified_without_altering_quota() {
        let mut usage = UsageInfo {
            primary: Some(super::super::WindowUsage {
                used_percent: Some(25.0),
                ..Default::default()
            }),
            ..Default::default()
        };
        apply_metadata_results(
            &mut usage,
            Some(Err(metadata_error(Some(reqwest::StatusCode::FORBIDDEN)))),
            Some(Err(metadata_error(Some(
                reqwest::StatusCode::TOO_MANY_REQUESTS,
            )))),
            None,
            1,
        );
        assert_eq!(usage.primary.unwrap().used_percent, Some(25.0));
        assert_eq!(usage.daybreak_error.as_deref(), Some("HTTP 403"));
        assert_eq!(usage.subscription_error.as_deref(), Some("HTTP 429"));
        assert!(
            usage
                .daybreak_error
                .as_deref()
                .is_none_or(|error| !error.contains("token"))
        );
    }

    #[test]
    fn metadata_requests_preserve_routing_and_required_query_headers() {
        let client = reqwest::Client::new();
        let models = build_models_request(&client, "secret", "acct", true)
            .build()
            .unwrap();
        assert_eq!(models.headers()["chatgpt-account-id"], "acct");
        assert_eq!(models.headers()["x-openai-fedramp"], "true");
        assert!(models.url().query_pairs().any(|(key, value)| {
            key == "client_version" && value == crate::auth::ALIGNED_CODEX_VERSION
        }));

        let subscriptions = build_subscriptions_request(&client, "secret", "acct", true)
            .build()
            .unwrap();
        assert_eq!(subscriptions.headers()["chatgpt-account-id"], "acct");
        assert_eq!(subscriptions.headers()["content-type"], "application/json");
        assert!(
            subscriptions
                .url()
                .query()
                .is_some_and(|query| query.contains("account_id=acct"))
        );
    }
}
