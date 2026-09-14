import { Pool, type PoolClient } from "pg";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} must be set`);
  }
  return value;
}

const globalForPool = globalThis as unknown as { pgPool?: Pool };

export const pool: Pool =
  globalForPool.pgPool ??
  new Pool({
    host: process.env.PGHOST ?? "localhost",
    port: Number(process.env.PGPORT ?? 5433),
    database: process.env.PGDATABASE ?? "threshold",
    user: "threshold_api_user",
    password: requireEnv("THRESHOLD_API_PASSWORD"),
    max: 10,
    statement_timeout: 10_000,
  });

if (process.env.NODE_ENV !== "production") {
  globalForPool.pgPool = pool;
}

export async function withTenantConnection<T>(
  clinicId: string,
  fn: (client: PoolClient) => Promise<T>,
): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query("SELECT set_config('app.current_clinic_id', $1, true)", [clinicId]);
    const result = await fn(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}
