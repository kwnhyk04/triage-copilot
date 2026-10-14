// Preloaded with `node --import` so tracing patches http before Fastify loads.
import { startTelemetry } from '@triage/ts-telemetry';

startTelemetry('gateway');
