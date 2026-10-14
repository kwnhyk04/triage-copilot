// Integration tests: need `make infra-up` and the variables from .env.
import base from '../../jest.config.base.js';

export default {
  ...base,
  testMatch: ['<rootDir>/src/**/*.int.test.ts'],
  testPathIgnorePatterns: ['/node_modules/'],
};
