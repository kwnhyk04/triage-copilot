// Lints all TypeScript packages; type-aware rules read each package's tsconfig.
import js from '@eslint/js';
import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import tseslint from 'typescript-eslint';

export default defineConfig([
  globalIgnores([
    '**/.next/',
    '**/.turbo/',
    '**/dist/',
    '**/coverage/',
    '**/generated/',
    '**/next-env.d.ts',
    '.venv/',
  ]),
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
  },
  { files: ['**/*.js'], extends: [tseslint.configs.disableTypeChecked] },
  {
    files: ['apps/web/**'],
    extends: [nextVitals],
    settings: { next: { rootDir: 'apps/web/' } },
  },
]);
