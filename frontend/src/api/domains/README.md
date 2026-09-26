# API domain facades (P2)

Each file re-exports a typed subset of `apiMethods` for discoverability.
Implementation remains in `../methods.ts` to avoid arrow-body parse hazards;
http helpers live in `../http.ts`, shared types in `../types.ts`.
