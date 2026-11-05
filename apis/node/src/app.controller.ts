import { BadRequestException, Body, Controller, Get, HttpCode, Inject, Post, Query } from '@nestjs/common';
import bcrypt from 'bcrypt';
import type pg from 'pg';
import { PG_POOL } from './db.js';

const BCRYPT_COST = 10;
const MAX_PASSWORD_BYTES = 72;
const DEFAULT_PRIMES_N = 1_000_000;
const MAX_PRIMES_N = 10_000_000;
const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 1000;
const FANOUT_CALLS = 10;
const FANOUT_DELAY_MS = 50;

interface Order {
  id: number;
  customer: string;
  category: string;
  amount: number;
  quantity: number;
  tags: string[];
}

interface TopOrder {
  id: number;
  customer: string;
  revenue: number;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Reads an optional integer query parameter within [lo, hi]. */
function intQuery(raw: string | undefined, def: number, lo: number, hi: number, message: string): number {
  if (raw === undefined) return def;
  if (!/^-?\d+$/.test(raw)) throw new BadRequestException(message);
  const value = Number(raw);
  if (value < lo || value > hi) throw new BadRequestException(message);
  return value;
}

/** Returns how many primes are <= n and the largest of them. */
function sieve(n: number): { count: number; largest: number } {
  if (n < 2) return { count: 0, largest: 0 };
  const composite = new Uint8Array(n + 1);
  for (let i = 2; i * i <= n; i++) {
    if (composite[i] === 0) {
      for (let j = i * i; j <= n; j += i) composite[j] = 1;
    }
  }
  let count = 0;
  let largest = 0;
  for (let k = 2; k <= n; k++) {
    if (composite[k] === 0) {
      count++;
      largest = k;
    }
  }
  return { count, largest };
}

@Controller()
export class AppController {
  constructor(@Inject(PG_POOL) private readonly db: pg.Pool) {}

  @Get('health')
  health() {
    return { status: 'ok' };
  }

  // The native `bcrypt` package hashes on libuv's thread pool (4 threads by
  // default), so the event loop stays free while a hash is computed.
  @Post('hash')
  @HttpCode(200)
  async hash(@Body() body: { password?: unknown } | undefined) {
    const password = body?.password;
    if (typeof password !== 'string' || password === '' || Buffer.byteLength(password) > MAX_PASSWORD_BYTES) {
      throw new BadRequestException('password must be 1-72 bytes');
    }
    return { hash: await bcrypt.hash(password, BCRYPT_COST) };
  }

  @Post('json/transform')
  @HttpCode(200)
  transform(@Body() body: { orders?: Order[] } | undefined) {
    const orders = body?.orders;
    if (!Array.isArray(orders)) throw new BadRequestException('orders must be an array');

    const groups = new Map<string, { count: number; revenue: number; amount: number }>();
    const tagCounts: Record<string, number> = {};
    const top: TopOrder[] = [];
    let matched = 0;

    for (const o of orders) {
      if (!(o.amount > 50)) continue;
      matched++;
      const revenue = o.amount * o.quantity;
      let g = groups.get(o.category);
      if (!g) {
        g = { count: 0, revenue: 0, amount: 0 };
        groups.set(o.category, g);
      }
      g.count++;
      g.revenue += revenue;
      g.amount += o.amount;
      for (const t of o.tags) tagCounts[t] = (tagCounts[t] ?? 0) + 1;
      top.push({ id: o.id, customer: o.customer, revenue });
    }

    top.sort((a, b) => b.revenue - a.revenue || a.id - b.id);
    const categories = [...groups]
      .map(([category, g]) => ({ category, count: g.count, revenue: g.revenue, avgAmount: g.amount / g.count }))
      .sort((a, b) => b.revenue - a.revenue || (a.category < b.category ? -1 : a.category > b.category ? 1 : 0));

    return {
      totalOrders: orders.length,
      matchedOrders: matched,
      categories,
      topOrders: top.slice(0, 10),
      tagCounts,
    };
  }

  // Runs on the event loop: while one sieve runs, every other request waits.
  @Get('compute/primes')
  primes(@Query('n') raw?: string) {
    const n = intQuery(raw, DEFAULT_PRIMES_N, 0, MAX_PRIMES_N, 'n must be an integer from 0 to 10000000');
    return { n, ...sieve(n) };
  }

  @Get('users')
  async users(@Query('limit') raw?: string) {
    const limit = intQuery(raw, DEFAULT_LIMIT, 1, MAX_LIMIT, 'limit must be an integer from 1 to 1000');
    const { rows } = await this.db.query(
      'SELECT id, name, email, created_at AS "createdAt" FROM users ORDER BY id LIMIT $1',
      [limit],
    );
    return rows;
  }

  // Simulates calling 10 downstream services in parallel, 50 ms each.
  @Get('fanout')
  async fanout() {
    const results = await Promise.all(
      Array.from({ length: FANOUT_CALLS }, async (_, i) => {
        await sleep(FANOUT_DELAY_MS);
        return { service: `service-${i}`, status: 'ok' };
      }),
    );
    return { calls: FANOUT_CALLS, results };
  }
}
