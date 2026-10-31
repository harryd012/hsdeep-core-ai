import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    // Node CommonJS test harnesses (the portal validation scripts). They are not
    // part of the Next.js application build - `next build`/`tsc` never compile
    // them - and `require()` is the ONLY valid module syntax in a `.cjs` file,
    // so the TypeScript ESM import rule does not apply to them.
    files: ["tests/**/*.cjs", "*.cjs"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
    },
  },
]);

export default eslintConfig;
