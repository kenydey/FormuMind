// ESLint exists for one reason: the two React hooks rules.
//
// `react-hooks/exhaustive-deps` finds effects and callbacks whose dependency list does not match
// what they read (stale closures) and dependencies that change on every render (`const x = a ?? []`
// in a dependency list re-runs the effect each time — it found one in MaterialSubstitutionModal).
// tsc cannot see either. It cannot know that a *prop* callback is unstable, which is how the
// AttachmentPreview request loop happened; that one is covered by its own regression test.
// Everything else stays with tsc and the tests — this is deliberately not a style linter.
import tsParser from "@typescript-eslint/parser";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["dist/**", "node_modules/**", "coverage/**"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
    },
  },
];
