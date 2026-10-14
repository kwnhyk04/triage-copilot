// Shared Jest setup: SWC strips types; *.int.test.ts files need infra and run via test:int.
const swc = (tsx) => [
  '@swc/jest',
  {
    jsc: {
      target: 'es2024',
      parser: { syntax: 'typescript', tsx },
      transform: { react: { runtime: 'automatic' } },
    },
  },
];

export default {
  testEnvironment: 'node',
  testMatch: ['<rootDir>/src/**/*.test.ts?(x)'],
  testPathIgnorePatterns: ['/node_modules/', '\\.int\\.test\\.ts$'],
  transform: {
    // TSX parsing reads `<T>(x: T) => x` as JSX, so plain .ts files are parsed without it.
    '^.+\\.ts$': swc(false),
    '^.+\\.tsx$': swc(true),
  },
};
