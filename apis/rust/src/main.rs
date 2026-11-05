//! Serves the benchmark contract (docs/design.md, section 2) with Axum on Tokio.

mod handlers;

use axum::{
    Router,
    extract::DefaultBodyLimit,
    routing::{get, post},
};
use sqlx::postgres::PgPoolOptions;

const MAX_BODY_BYTES: usize = 10 * 1024 * 1024;

#[tokio::main]
async fn main() {
    // Each connection needs a file descriptor. Containers start processes with
    // a soft limit of 1024 open files; Go, .NET and Node.js raise it to the hard
    // limit at startup, a Rust program does not. Without this, accept() fails
    // with "too many open files" past ~1000 connections (see the README).
    let nofile = rlimit::increase_nofile_limit(u64::MAX).expect("raise open-file limit");
    println!("open-file limit: {nofile}");

    let database_url = std::env::var("DATABASE_URL").expect("DATABASE_URL must be set");
    let db = PgPoolOptions::new()
        .max_connections(20)
        .connect(&database_url)
        .await
        .expect("connect to Postgres");

    let app = Router::new()
        .route("/health", get(handlers::health))
        .route("/hash", post(handlers::hash))
        .route("/json/transform", post(handlers::transform))
        .route("/compute/primes", get(handlers::primes))
        .route("/users", get(handlers::users))
        .route("/fanout", get(handlers::fanout))
        .layer(DefaultBodyLimit::max(MAX_BODY_BYTES))
        .with_state(db);

    let port = std::env::var("PORT").unwrap_or_else(|_| "8080".into());
    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{port}"))
        .await
        .expect("bind port");
    println!("Rust (Axum) listening on :{port}");
    axum::serve(listener, app).await.expect("server error");
}
