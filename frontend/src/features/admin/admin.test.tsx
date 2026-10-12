import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AdminPage } from "../../pages/AdminPage";
import {
  ProvidersPanel,
  RoutesPanel,
  LimitsPanel,
  SourcesPanel,
} from "./Management";
import { StatisticsPanel, JobsPanel } from "./Monitoring";
import { AudioAdmin } from "../audio/AudioAdmin";

const provider = {
  id: "provider",
  name: "Fixture",
  protocol: "openai_chat",
  base_url: "https://api.example.com/v1",
  host: "api.example.com",
  enabled: true,
  has_key: true,
  key_mask: "••••••••",
};
const model = {
  id: "model",
  provider_id: "provider",
  model_identifier: "fixture-model",
  enabled: true,
  capabilities: { max_context_tokens: 4096, mock_behavior: "success" },
  pricing: {},
};
function setup(overrides: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, "https://test.example").pathname;
    const data: Record<string, unknown> = {
      "/api/v1/admin/providers": { items: [provider], total: 1 },
      "/api/v1/admin/models": {
        items: [
          model,
          { ...model, id: "fallback", model_identifier: "fallback-model" },
        ],
        total: 2,
      },
      "/api/v1/admin/ai-status": { master_key_configured: true },
      "/api/v1/admin/quotas": {
        defaults: { books: 100 },
        overrides: { items: [], total: 0 },
      },
      "/api/v1/admin/audio/profiles": { items: [] },
      "/api/v1/admin/audio/routes": { items: [] },
      ...overrides,
    };
    if (path.endsWith("/csrf"))
      document.cookie = "knigoskop_csrf=" + "x".repeat(43) + "; path=/";
    return {
      ok: true,
      status: init?.method === "DELETE" ? 204 : 200,
      json: async () =>
        init?.method && init.method !== "GET"
          ? {}
          : data[path] || { items: [], total: 0 },
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
function mount(content: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      {content}
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.cookie = "knigoskop_csrf=; Max-Age=0; path=/";
});
const stats = {
  users_total: 3,
  jobs: { completed: 2, failed: 1 },
  llm: { calls: 3, input_tokens: 20, output_tokens: 5, mean_latency_ms: 200 },
  tts: { calls: 2, chars: 50, duration_ms: 2000 },
  costs: [
    { currency: "RUB", estimated_cost_micros: 1000000, unknown_cost_calls: 1 },
    { currency: "USD", estimated_cost_micros: 2000000, unknown_cost_calls: 0 },
  ],
  models: [],
  tts_profiles: [],
};
test("navigation loads only the selected administrative section", async () => {
  const fetch = setup({ "/api/v1/admin/statistics": stats });
  mount(<AdminPage />);
  await screen.findByRole("heading", { name: "Пользователи" });
  expect(
    fetch.mock.calls.some(([url]) => url.includes("/admin/statistics")),
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Статистика" }));
  await screen.findByText("RUB");
  expect(screen.queryByRole("heading", { name: "Пользователи" })).toBeNull();
});
test("provider key is submitted with CSRF and cleared", async () => {
  const fetch = setup();
  mount(<ProvidersPanel />);
  await screen.findByText("Fixture");
  fireEvent.change(screen.getByLabelText("Название провайдера"), {
    target: { value: "New provider" },
  });
  fireEvent.change(screen.getByLabelText("URL API"), {
    target: { value: "https://api.example.com/v1" },
  });
  const key = screen.getByLabelText("Новый API-ключ");
  expect(key).toHaveAttribute("type", "password");
  fireEvent.change(key, { target: { value: "fixture-secret-key" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить провайдера" }));
  await screen.findByText("Настройки сохранены.");
  const call = fetch.mock.calls.find(
    ([url, init]) =>
      url === "/api/v1/admin/providers" && init?.method === "POST",
  );
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    api_key: "fixture-secret-key",
    host: "api.example.com",
  });
  expect(call?.[1]?.headers).toHaveProperty("X-CSRF-Token", "x".repeat(43));
  expect(key).toHaveValue("");
});
test("route form submits primary fallback and budget in micro units", async () => {
  const fetch = setup();
  mount(<RoutesPanel />);
  await screen.findAllByText("fixture-model");
  fireEvent.change(screen.getByLabelText("Резервная модель"), {
    target: { value: "fallback" },
  });
  fireEvent.change(screen.getByLabelText("Бюджет одной задачи"), {
    target: { value: "2.5" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Сохранить версию маршрута" }),
  );
  await screen.findByText("Настройки сохранены.");
  const call = fetch.mock.calls.find(
    ([url, init]) => url === "/api/v1/admin/routes" && init?.method === "POST",
  );
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    primary_model_id: "model",
    fallback_model_id: "fallback",
    policy: { max_cost_micros: 2500000, currency: "RUB" },
  });
});
test("statistics separate currencies and identify unknown cost", async () => {
  setup({ "/api/v1/admin/statistics": stats });
  mount(<StatisticsPanel />);
  await screen.findByText("RUB");
  expect(screen.getByText("USD")).toBeVisible();
  expect(screen.getByText("1.000000")).toBeVisible();
  expect(screen.getByText("2.000000")).toBeVisible();
  expect(
    screen.getByText(/Неизвестная стоимость не считается нулевой/),
  ).toBeVisible();
});
test("job cancellation needs explicit confirmation", async () => {
  const job = {
    id: "job",
    user_id: "user",
    type: "summary",
    status: "queued",
    progress: 0,
    attempts: 0,
    error_code: null,
    created_at: "2026-10-13",
  };
  const fetch = setup({
    "/api/v1/admin/job-monitor": { items: [job], total: 1 },
  });
  mount(<JobsPanel />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Отменить задачу job" }),
  );
  expect(fetch.mock.calls.some(([url]) => url.endsWith("/cancel"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить отмену" }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([url, init]) =>
          url === "/api/v1/admin/jobs/job/cancel" && init?.method === "POST",
      ),
    ).toBe(true),
  );
});
test("quota form sets a personal book limit", async () => {
  const fetch = setup();
  mount(<LimitsPanel />);
  await screen.findByText("100");
  fireEvent.change(screen.getByLabelText("Метрика лимита"), {
    target: { value: "books" },
  });
  fireEvent.change(screen.getByLabelText("Значение лимита"), {
    target: { value: "3" },
  });
  fireEvent.change(screen.getByLabelText("ID пользователя (необязательно)"), {
    target: { value: "12345678-1234-1234-1234-123456789012" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить лимит" }));
  await screen.findByText("Настройки сохранены.");
  const call = fetch.mock.calls.find(
    ([url, init]) => url === "/api/v1/admin/quotas" && init?.method === "PUT",
  );
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    metric: "books",
    limit_value: 3,
  });
});
test("source form defaults to disabled with no import approvals", async () => {
  const fetch = setup();
  mount(<SourcesPanel />);
  fireEvent.change(screen.getByLabelText("Название источника"), {
    target: { value: "Allowed catalog" },
  });
  fireEvent.change(screen.getByLabelText("URL каталога"), {
    target: { value: "https://catalog.example.com/index.json" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить источник" }));
  await screen.findByText("Настройки сохранены.");
  const call = fetch.mock.calls.find(
    ([url, init]) => url === "/api/v1/admin/sources" && init?.method === "POST",
  );
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    enabled: false,
    approvals: [],
    host: "catalog.example.com",
  });
});
test("TTS key is submitted explicitly and cleared after saving", async () => {
  const fetch = setup();
  mount(<AudioAdmin />);
  await screen.findByRole("heading", { name: "Настройки озвучивания" });
  for (const [label, value] of [
    ["Название профиля", "GenVoice"],
    ["Базовый URL провайдера", "https://api.genvoice.ru/v1"],
    [
      "Идентификаторы голосов через запятую",
      "f561cd20-a52a-4551-a1e3-f51f0a45c9fd",
    ],
    ["Цена за 1000 символов", "1"],
    ["Валюта цены", "RUB"],
  ])
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  const key = screen.getByLabelText("API-ключ TTS");
  fireEvent.change(key, { target: { value: "tts-fixture-secret" } });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Сохранить профиль озвучивания" }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Сохранить профиль озвучивания" }),
  );
  await waitFor(() => expect(key).toHaveValue(""));
  const call = fetch.mock.calls.find(
    ([url, init]) =>
      url === "/api/v1/admin/audio/profiles" && init?.method === "POST",
  );
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    api_key: "tts-fixture-secret",
    price_micros_per_1000_chars: 1000000,
  });
});
