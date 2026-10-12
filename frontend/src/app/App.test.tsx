import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("startup page", () => {
  it("renders the Russian product name and scaffold status", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("Книгоскоп");
    expect(html).toContain("Начало разработки");
    expect(html).toContain("/api/v1/status");
  });
});
