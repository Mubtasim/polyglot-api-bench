use std::{cmp::Ordering, collections::HashMap, time::Duration};

use axum::{
    Json,
    extract::{Query, State, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sqlx::PgPool;

const BCRYPT_COST: u32 = 10;
const MAX_PASSWORD_LEN: usize = 72;
const DEFAULT_PRIMES_N: i64 = 1_000_000;
const MAX_PRIMES_N: i64 = 10_000_000;
const DEFAULT_LIMIT: i64 = 50;
const MAX_LIMIT: i64 = 1000;
const FANOUT_CALLS: usize = 10;
const FANOUT_DELAY: Duration = Duration::from_millis(50);

/// The contract's error shape: 400 with `{"error": "..."}`.
pub struct ApiError(StatusCode, String);

impl ApiError {
    fn bad_request(msg: impl Into<String>) -> Self {
        Self(StatusCode::BAD_REQUEST, msg.into())
    }
}

impl From<JsonRejection> for ApiError {
    fn from(_: JsonRejection) -> Self {
        Self::bad_request("invalid JSON body")
    }
}

impl From<sqlx::Error> for ApiError {
    fn from(_: sqlx::Error) -> Self {
        Self(StatusCode::INTERNAL_SERVER_ERROR, "database error".into())
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({ "error": self.1 }))).into_response()
    }
}

type ApiResult<T> = Result<Json<T>, ApiError>;

/// Reads an optional integer query parameter within [lo, hi].
fn int_query(
    q: &HashMap<String, String>,
    name: &str,
    default: i64,
    lo: i64,
    hi: i64,
) -> Option<i64> {
    match q.get(name) {
        None => Some(default),
        Some(raw) => raw.parse::<i64>().ok().filter(|v| (lo..=hi).contains(v)),
    }
}

pub async fn health() -> Json<Value> {
    Json(json!({ "status": "ok" }))
}

#[derive(Deserialize)]
pub struct HashRequest {
    password: String,
}

pub async fn hash(body: Result<Json<HashRequest>, JsonRejection>) -> ApiResult<Value> {
    let Json(req) = body?;
    if req.password.is_empty() || req.password.len() > MAX_PASSWORD_LEN {
        return Err(ApiError::bad_request("password must be 1-72 bytes"));
    }
    // bcrypt takes tens of milliseconds of CPU on purpose. Running it on an async
    // worker would stall every other task on that thread, so it goes to Tokio's
    // blocking pool instead.
    let hashed = tokio::task::spawn_blocking(move || bcrypt::hash(req.password, BCRYPT_COST))
        .await
        .map_err(|_| ApiError(StatusCode::INTERNAL_SERVER_ERROR, "hash task failed".into()))?
        .map_err(|_| ApiError(StatusCode::INTERNAL_SERVER_ERROR, "hash failed".into()))?;
    Ok(Json(json!({ "hash": hashed })))
}

#[derive(Deserialize)]
pub struct Order {
    id: i64,
    customer: String,
    category: String,
    amount: f64,
    quantity: i64,
    tags: Vec<String>,
}

#[derive(Deserialize)]
pub struct TransformRequest {
    orders: Vec<Order>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TransformResponse<'a> {
    total_orders: usize,
    matched_orders: usize,
    categories: Vec<CategoryStats<'a>>,
    top_orders: Vec<TopOrder<'a>>,
    tag_counts: HashMap<&'a str, u64>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CategoryStats<'a> {
    category: &'a str,
    count: u64,
    revenue: f64,
    avg_amount: f64,
}

#[derive(Serialize)]
pub struct TopOrder<'a> {
    id: i64,
    customer: &'a str,
    revenue: f64,
}

#[derive(Default)]
struct Agg {
    count: u64,
    revenue: f64,
    amount: f64,
}

pub async fn transform(
    body: Result<Json<TransformRequest>, JsonRejection>,
) -> Result<Response, ApiError> {
    let Json(req) = body?;
    let mut groups: HashMap<&str, Agg> = HashMap::new();
    let mut tag_counts: HashMap<&str, u64> = HashMap::new();
    let mut top: Vec<TopOrder> = Vec::with_capacity(req.orders.len());
    let mut matched = 0;

    for o in req.orders.iter().filter(|o| o.amount > 50.0) {
        matched += 1;
        let revenue = o.amount * o.quantity as f64;
        let g = groups.entry(&o.category).or_default();
        g.count += 1;
        g.revenue += revenue;
        g.amount += o.amount;
        for t in &o.tags {
            *tag_counts.entry(t).or_default() += 1;
        }
        top.push(TopOrder {
            id: o.id,
            customer: &o.customer,
            revenue,
        });
    }

    top.sort_by(|a, b| b.revenue.total_cmp(&a.revenue).then(a.id.cmp(&b.id)));
    top.truncate(10);

    let mut categories: Vec<CategoryStats> = groups
        .into_iter()
        .map(|(category, g)| CategoryStats {
            category,
            count: g.count,
            revenue: g.revenue,
            avg_amount: g.amount / g.count as f64,
        })
        .collect();
    categories.sort_by(|a, b| match b.revenue.total_cmp(&a.revenue) {
        Ordering::Equal => a.category.cmp(b.category),
        other => other,
    });

    // The response borrows strings from the request, so serialize before `req` is dropped.
    let response = TransformResponse {
        total_orders: req.orders.len(),
        matched_orders: matched,
        categories,
        top_orders: top,
        tag_counts,
    };
    Ok(Json(response).into_response())
}

pub async fn primes(Query(q): Query<HashMap<String, String>>) -> ApiResult<Value> {
    let n = int_query(&q, "n", DEFAULT_PRIMES_N, 0, MAX_PRIMES_N)
        .ok_or_else(|| ApiError::bad_request("n must be an integer from 0 to 10000000"))?;
    let (count, largest) = sieve(n as usize);
    Ok(Json(json!({ "n": n, "count": count, "largest": largest })))
}

/// Returns how many primes are <= n and the largest of them.
fn sieve(n: usize) -> (usize, usize) {
    if n < 2 {
        return (0, 0);
    }
    let mut composite = vec![false; n + 1];
    let mut i = 2;
    while i * i <= n {
        if !composite[i] {
            for j in (i * i..=n).step_by(i) {
                composite[j] = true;
            }
        }
        i += 1;
    }
    let (mut count, mut largest) = (0, 0);
    for (k, &is_composite) in composite.iter().enumerate().skip(2) {
        if !is_composite {
            count += 1;
            largest = k;
        }
    }
    (count, largest)
}

#[derive(Serialize, sqlx::FromRow)]
#[serde(rename_all = "camelCase")]
pub struct User {
    id: i32,
    name: String,
    email: String,
    created_at: DateTime<Utc>,
}

pub async fn users(
    State(db): State<PgPool>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult<Vec<User>> {
    let limit = int_query(&q, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        .ok_or_else(|| ApiError::bad_request("limit must be an integer from 1 to 1000"))?;
    let users = sqlx::query_as::<_, User>(
        "SELECT id, name, email, created_at FROM users ORDER BY id LIMIT $1",
    )
    .bind(limit)
    .fetch_all(&db)
    .await?;
    Ok(Json(users))
}

/// Simulates calling 10 downstream services in parallel, 50 ms each.
pub async fn fanout() -> Json<Value> {
    let calls = (0..FANOUT_CALLS).map(|i| async move {
        tokio::time::sleep(FANOUT_DELAY).await;
        json!({ "service": format!("service-{i}"), "status": "ok" })
    });
    let results = futures_util::future::join_all(calls).await;
    Json(json!({ "calls": FANOUT_CALLS, "results": results }))
}
