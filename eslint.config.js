import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  { ignores: ['**/out/**', '**/dist/**', '**/release/**', '**/node_modules/**', '.pnpm-store/**', 'test-results/**', '**/electron.vite.config.*.mjs', 'backend/.venv/**', 'backend/app/static/**'] },
  js.configs.recommended,
  { files: ['scripts/*.{cjs,mjs}'], languageOptions: { globals: { require: 'readonly', process: 'readonly', __filename: 'readonly', console: 'readonly', setTimeout: 'readonly' } } },
  ...tseslint.configs.recommended,
  { files: ['scripts/*.cjs'], rules: { '@typescript-eslint/no-require-imports': 'off' } },
  { rules: { '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }] } },
  { files: ['**/*.{ts,tsx}'], plugins: { 'react-hooks': reactHooks }, rules: reactHooks.configs.recommended.rules },
);
