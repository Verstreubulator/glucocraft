// License: AGPL-3.0-only (LICENSE).
// ESLint configuration (ESLint 9, flat configuration) of the dashboard files in custom_components/glucocraft/www/.
// The files are ES modules: Home Assistant loads them as module resources (frontend.py, res_type "module"), and each
// imports the language module and the shared module next to its own URL (import.meta.url). The rules: ESLint's
// recommended set as errors (js.configs.recommended of ESLint 9, written out here because the test workflow installs
// ESLint alone, without the @eslint/js package; tests/test_javascript_lint.py compares the list with the installed
// ESLint's own), and two warnings: the line length of the Python (ruff: 120 columns, pyproject.toml) and no ternary
// inside a ternary. The globals are those of the browser and of Home Assistant's frontend that the files use. Run from
// the repository root (tests/README.md):
//   npx eslint@9 custom_components/glucocraft/www
"use strict";

// js.configs.recommended (ESLint 9)
const RECOMMENDED = [
  "constructor-super", "for-direction", "getter-return", "no-async-promise-executor", "no-case-declarations",
  "no-class-assign", "no-compare-neg-zero", "no-cond-assign", "no-const-assign", "no-constant-binary-expression",
  "no-constant-condition", "no-control-regex", "no-debugger", "no-delete-var", "no-dupe-args", "no-dupe-class-members",
  "no-dupe-else-if", "no-dupe-keys", "no-duplicate-case", "no-empty", "no-empty-character-class", "no-empty-pattern",
  "no-empty-static-block", "no-ex-assign", "no-extra-boolean-cast", "no-fallthrough", "no-func-assign",
  "no-global-assign", "no-import-assign", "no-invalid-regexp", "no-irregular-whitespace", "no-loss-of-precision",
  "no-misleading-character-class", "no-new-native-nonconstructor", "no-nonoctal-decimal-escape", "no-obj-calls",
  "no-octal", "no-prototype-builtins", "no-redeclare", "no-regex-spaces", "no-self-assign", "no-setter-return",
  "no-shadow-restricted-names", "no-sparse-arrays", "no-this-before-super", "no-undef", "no-unexpected-multiline",
  "no-unreachable", "no-unsafe-finally", "no-unsafe-negation", "no-unsafe-optional-chaining", "no-unused-labels",
  "no-unused-private-class-members", "no-unused-vars", "no-useless-backreference", "no-useless-catch",
  "no-useless-escape", "no-with", "require-yield", "use-isnan", "valid-typeof",
];

// the browser's globals the files use (the language's own, such as globalThis, come with ecmaVersion)
const GLOBALS = [
  "window", "document", "console", "customElements", "HTMLElement", "CustomEvent", "ResizeObserver", "NodeFilter",
  "setTimeout", "clearTimeout", "setInterval", "clearInterval", "URL", "TextDecoder", "localStorage", "history",
  "location", "matchMedia", "confirm", "fetch",
];

module.exports = [
  {
    files: ["custom_components/glucocraft/www/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: Object.fromEntries(GLOBALS.map((name) => [name, "readonly"])),
    },
    linterOptions: { reportUnusedDisableDirectives: "warn" },
    rules: {
      ...Object.fromEntries(RECOMMENDED.map((rule) => [rule, "error"])),
      "max-len": ["warn", { code: 120, ignoreUrls: true }],
      "no-nested-ternary": "warn",
    },
  },
];
