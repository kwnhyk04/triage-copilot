import { HttpInstrumentation } from '@opentelemetry/instrumentation-http';
import { NodeSDK } from '@opentelemetry/sdk-node';

/**
 * Starts OTLP tracing when OTEL_EXPORTER_OTLP_ENDPOINT is set; the SDK reads the other OTEL_* vars.
 * Preload it (node --import) so http is patched before the server module loads.
 */
export function startTelemetry(serviceName: string): void {
  if (!process.env.OTEL_EXPORTER_OTLP_ENDPOINT) return;
  const sdk = new NodeSDK({ serviceName, instrumentations: [new HttpInstrumentation()] });
  sdk.start();
  process.once('SIGTERM', () => void sdk.shutdown());
}
