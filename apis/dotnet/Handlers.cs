using System.Text;
using Npgsql;

namespace DotnetApi;

public static class Handlers
{
    private const int BcryptCost = 10;
    private const int MaxPasswordBytes = 72;
    private const int DefaultPrimesN = 1_000_000;
    private const int MaxPrimesN = 10_000_000;
    private const int DefaultLimit = 50;
    private const int MaxLimit = 1000;
    private const int FanoutCalls = 10;
    private static readonly TimeSpan FanoutDelay = TimeSpan.FromMilliseconds(50);

    private static IResult BadRequest(string message) => Results.BadRequest(new { error = message });

    public static IResult Hash(HashRequest req)
    {
        if (string.IsNullOrEmpty(req.Password) || Encoding.UTF8.GetByteCount(req.Password) > MaxPasswordBytes)
            return BadRequest("password must be 1-72 bytes");
        // Runs synchronously on a thread-pool thread, the usual way to call BCrypt.Net.
        return Results.Ok(new { hash = BCrypt.Net.BCrypt.HashPassword(req.Password, BcryptCost) });
    }

    public static IResult Transform(TransformRequest req)
    {
        var groups = new Dictionary<string, CategoryAgg>();
        var tagCounts = new Dictionary<string, int>();
        var top = new List<TopOrder>(req.Orders.Count);
        var matched = 0;

        foreach (var o in req.Orders)
        {
            if (!(o.Amount > 50)) continue;
            matched++;
            var revenue = o.Amount * o.Quantity;
            if (!groups.TryGetValue(o.Category, out var g))
            {
                g = new CategoryAgg();
                groups[o.Category] = g;
            }
            g.Count++;
            g.Revenue += revenue;
            g.Amount += o.Amount;
            foreach (var t in o.Tags)
                tagCounts[t] = tagCounts.GetValueOrDefault(t) + 1;
            top.Add(new TopOrder(o.Id, o.Customer, revenue));
        }

        top.Sort((a, b) => b.Revenue != a.Revenue ? b.Revenue.CompareTo(a.Revenue) : a.Id.CompareTo(b.Id));
        var categories = groups
            .Select(kv => new CategoryStats(kv.Key, kv.Value.Count, kv.Value.Revenue, kv.Value.Amount / kv.Value.Count))
            .OrderByDescending(c => c.Revenue)
            .ThenBy(c => c.Category, StringComparer.Ordinal)
            .ToList();

        return Results.Ok(new TransformResponse(req.Orders.Count, matched, categories, top.Take(10).ToList(), tagCounts));
    }

    public static IResult Primes(int? n)
    {
        var value = n ?? DefaultPrimesN;
        if (value is < 0 or > MaxPrimesN)
            return BadRequest("n must be an integer from 0 to 10000000");
        var (count, largest) = Sieve(value);
        return Results.Ok(new { n = value, count, largest });
    }

    /// <summary>Returns how many primes are &lt;= n and the largest of them.</summary>
    private static (int Count, int Largest) Sieve(int n)
    {
        if (n < 2) return (0, 0);
        var composite = new bool[n + 1];
        for (var i = 2; (long)i * i <= n; i++)
        {
            if (composite[i]) continue;
            for (var j = i * i; j <= n; j += i) composite[j] = true;
        }
        int count = 0, largest = 0;
        for (var k = 2; k <= n; k++)
        {
            if (composite[k]) continue;
            count++;
            largest = k;
        }
        return (count, largest);
    }

    public static async Task<IResult> Users(int? limit, NpgsqlDataSource db)
    {
        var value = limit ?? DefaultLimit;
        if (value is < 1 or > MaxLimit)
            return BadRequest("limit must be an integer from 1 to 1000");

        await using var cmd = db.CreateCommand("SELECT id, name, email, created_at FROM users ORDER BY id LIMIT $1");
        cmd.Parameters.Add(new NpgsqlParameter { Value = value });
        await using var reader = await cmd.ExecuteReaderAsync();
        var users = new List<User>(value);
        while (await reader.ReadAsync())
            users.Add(new User(reader.GetInt32(0), reader.GetString(1), reader.GetString(2), reader.GetFieldValue<DateTime>(3)));
        return Results.Ok(users);
    }

    /// <summary>Simulates calling 10 downstream services in parallel, 50 ms each.</summary>
    public static async Task<IResult> Fanout()
    {
        var calls = Enumerable.Range(0, FanoutCalls).Select(async i =>
        {
            await Task.Delay(FanoutDelay);
            return new CallResult($"service-{i}", "ok");
        });
        var results = await Task.WhenAll(calls);
        return Results.Ok(new { calls = FanoutCalls, results });
    }
}

public record HashRequest(string? Password);
public record Order(long Id, string Customer, string Category, double Amount, int Quantity, List<string> Tags);
public record TransformRequest(List<Order> Orders);
public record TopOrder(long Id, string Customer, double Revenue);
public record CategoryStats(string Category, int Count, double Revenue, double AvgAmount);
public record TransformResponse(int TotalOrders, int MatchedOrders, List<CategoryStats> Categories, List<TopOrder> TopOrders, Dictionary<string, int> TagCounts);
public record User(int Id, string Name, string Email, DateTime CreatedAt);
public record CallResult(string Service, string Status);

public sealed class CategoryAgg
{
    public int Count;
    public double Revenue;
    public double Amount;
}
