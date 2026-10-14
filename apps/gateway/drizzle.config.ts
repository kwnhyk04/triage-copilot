import { loadConfig, z } from '@triage/ts-config';
import { defineConfig } from 'drizzle-kit';

const { GATEWAY_DATABASE_URL } = loadConfig(z.object({ GATEWAY_DATABASE_URL: z.url() }));

// Runs as the gateway role, so migrations can only touch the gateway schema they are tracked in.
export default defineConfig({
  dialect: 'postgresql',
  schema: './src/schema.ts',
  out: './drizzle',
  dbCredentials: { url: GATEWAY_DATABASE_URL },
  schemaFilter: ['gateway'],
  migrations: { schema: 'gateway' },
});
