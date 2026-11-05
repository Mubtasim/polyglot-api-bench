FROM rust:1.99.1-slim-trixie AS build
WORKDIR /src
# Build the dependencies first, in their own layer, so code changes rebuild only this crate.
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs && cargo build --release --locked && rm -rf src
COPY src ./src
RUN touch src/main.rs && cargo build --release --locked

FROM gcr.io/distroless/cc-debian13:nonroot@sha256:08476c244888bfb425bae1ae4dd7c17f093e972aab8426883d97f3c6f280f7c1
COPY --from=build /src/target/release/rust-api /rust-api
ENV PORT=8080
EXPOSE 8080
ENTRYPOINT ["/rust-api"]
