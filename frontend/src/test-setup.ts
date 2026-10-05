import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// `waitFor` / `findBy*` give up after 1 s by default. The suite runs 130 files in parallel jsdom workers, and on a
// loaded CI runner a component that resolves a mocked promise and re-renders can take longer than that: the
// reviewer-checklist test failed once in CI ("Unable to find an element by: [data-testid=reviewer-checklist-item]")
// and passed 3/3 locally. A longer ceiling costs nothing when the element appears (the wait returns as soon as it
// does) and only delays a genuine failure.
configure({ asyncUtilTimeout: 5000 });
