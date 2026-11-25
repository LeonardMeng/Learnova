// A tiny fake of the Learnova API for tests: register handlers per "METHOD /path", and every
// call is recorded with its parsed JSON body.
import { vi } from "vitest";

type Handler = (body: any, url: URL) => { status?: number; json: unknown };

export function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: { method: string; path: string; body: any }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: url.pathname + url.search, body });
    const route = routes[`${method} ${url.pathname}`];
    if (route === undefined) return new Response(JSON.stringify({ error: "not mocked", code: "x" }), { status: 404 });
    const out = typeof route === "function" ? (route as Handler)(body, url) : { json: route };
    return new Response(JSON.stringify(out.json), { status: out.status ?? 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}
