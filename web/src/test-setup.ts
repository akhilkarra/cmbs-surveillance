// Testing Library unmounts what a test rendered after each test.
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);
