import { Ajv2020, type ValidateFunction } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

import { type Messages, schemas } from './generated/messages.ts';

export type * from './generated/messages.ts';

const ajv = new Ajv2020({ allErrors: true, allowUnionTypes: true });
// ajv-formats is CommonJS; Node's ESM interop puts its plugin function on `default`.
addFormats.default(ajv);
// Keys match the file names, so cross-file $refs such as step-recorded.v1.schema.json resolve.
for (const [name, schema] of Object.entries(schemas)) ajv.addSchema(schema, `${name}.schema.json`);

/** Validates `data` against a message contract and returns it typed; throws naming every violation. */
export function parseMessage<K extends keyof Messages>(name: K, data: unknown): Messages[K] {
  // No message schema is $async, so every validator is synchronous.
  const validate = ajv.getSchema(`${name}.schema.json`) as ValidateFunction<Messages[K]>;
  if (validate(data)) return data;
  throw new Error(`invalid ${name} message: ${ajv.errorsText(validate.errors)}`);
}
