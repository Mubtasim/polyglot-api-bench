// Serves the benchmark contract (docs/design.md, section 2) with ASP.NET Core Minimal APIs.
using DotnetApi;
using Npgsql;

var builder = WebApplication.CreateBuilder(args);
builder.Logging.ClearProviders(); // no request logging
builder.WebHost.ConfigureKestrel(o => o.Limits.MaxRequestBodySize = 10 * 1024 * 1024);
builder.WebHost.UseUrls($"http://0.0.0.0:{Environment.GetEnvironmentVariable("PORT") ?? "8080"}");

// Make binding failures (bad JSON, non-integer query values) throw, so the
// handler below can return the contract's {"error": "..."} body.
builder.Services.Configure<RouteHandlerOptions>(o => o.ThrowOnBadRequest = true);

var connectionString = Environment.GetEnvironmentVariable("DB_CONNECTION")
    ?? "Host=localhost;Username=bench;Password=bench;Database=bench;Maximum Pool Size=20;Gss Encryption Mode=Disable";
builder.Services.AddSingleton(NpgsqlDataSource.Create(connectionString));

var app = builder.Build();

app.Use(async (context, next) =>
{
    try
    {
        await next(context);
    }
    catch (BadHttpRequestException)
    {
        context.Response.StatusCode = StatusCodes.Status400BadRequest;
        await context.Response.WriteAsJsonAsync(new { error = "invalid request" });
    }
});

app.MapGet("/health", () => new { status = "ok" });
app.MapPost("/hash", Handlers.Hash);
app.MapPost("/json/transform", Handlers.Transform);
app.MapGet("/compute/primes", Handlers.Primes);
app.MapGet("/users", Handlers.Users);
app.MapGet("/fanout", Handlers.Fanout);

Console.WriteLine(".NET (ASP.NET Core) listening on :8080");
app.Run();
