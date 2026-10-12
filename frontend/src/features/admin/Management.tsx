import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, errorMessage, request } from "../../api/auth";

type Page<T> = { items: T[]; total: number };
export interface Provider {
  id: string;
  name: string;
  protocol: string;
  base_url: string;
  host: string;
  enabled: boolean;
  has_key: boolean;
  key_mask: string | null;
}
export interface Model {
  id: string;
  provider_id: string;
  model_identifier: string;
  enabled: boolean;
  capabilities: { max_context_tokens: number; mock_behavior: string };
  pricing?: {
    input_micros_per_million?: number;
    output_micros_per_million?: number;
    currency?: string;
  };
}
export const tasks: Record<string, string> = {
  diagnostic: "Диагностика",
  structure: "Структура",
  summary_short: "Краткое резюме",
  summary_medium: "Среднее резюме",
  summary_detailed: "Подробное резюме",
  validation: "Проверка",
};
export const metrics: Record<string, string> = {
  daily_jobs: "Задач в сутки",
  daily_requests: "Запросов в сутки",
  daily_input_chars: "Символов текста в сутки",
  daily_tokens: "Токенов в сутки",
  active_jobs: "Одновременных задач",
  daily_tts_chars: "Символов TTS в сутки",
  books: "Книг в библиотеке",
  storage_bytes: "Место для файлов, байты",
};
function useResource<T>(endpoint: string) {
  return useQuery({
    queryKey: ["management", endpoint],
    queryFn: () => request<T>(endpoint),
    retry: false,
  });
}
function useChoices<T>(endpoint: string) {
  return useQuery({
    queryKey: ["management-choices", endpoint],
    retry: false,
    queryFn: async () => {
      const items: T[] = [];
      let total = 0;
      do {
        const page = await request<Page<T>>(
          `${endpoint}?offset=${items.length}&limit=100`,
        );
        total = page.total;
        if (total > 10000 || (!page.items.length && items.length < total))
          throw new ApiError(
            422,
            "config_list_limit",
            "Список настроек слишком большой или изменился; обновите страницу",
          );
        items.push(...page.items);
      } while (items.length < total);
      return { items, total };
    },
  });
}
function useAction() {
  const cache = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      await cache.invalidateQueries();
      setNotice("Настройки сохранены.");
      return true;
    } catch (e) {
      setError(errorMessage(e));
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, notice, run };
}
function Notice({ error, notice }: { error?: unknown; notice?: string }) {
  return (
    <>
      {error && (
        <p role="alert">
          {typeof error === "string" ? error : errorMessage(error)}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
    </>
  );
}
export function Pager({
  offset,
  total,
  onChange,
}: {
  offset: number;
  total: number;
  onChange: (v: number) => void;
}) {
  return (
    <nav aria-label="Страницы списка">
      <button
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - 20))}
      >
        Предыдущая страница
      </button>
      <button
        disabled={offset + 20 >= total}
        onClick={() => onChange(offset + 20)}
      >
        Следующая страница
      </button>
    </nav>
  );
}
function ListPanel({
  children,
  title,
}: {
  children: ReactNode;
  title: string;
}) {
  return (
    <section className="admin-section">
      <h2>{title}</h2>
      {children}
    </section>
  );
}
function clearKeys(form: HTMLFormElement) {
  form
    .querySelectorAll<HTMLInputElement>('input[type="password"]')
    .forEach((input) => (input.value = ""));
}

export function ProvidersPanel() {
  const [offset, setOffset] = useState(0);
  const data = useResource<Page<Provider>>(
    `/admin/providers?offset=${offset}&limit=20`,
  );
  const status = useResource<{ master_key_configured: boolean }>(
    "/admin/ai-status",
  );
  const [selected, setSelected] = useState<Provider | null>(null);
  const [protocol, setProtocol] = useState("openai_chat");
  const action = useAction();
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const d = new FormData(form);
    const url = String(d.get("base_url") || "");
    try {
      const payload = selected
        ? { name: d.get("name"), base_url: url }
        : {
            name: d.get("name"),
            protocol,
            base_url: protocol === "mock" ? "" : url,
            host: protocol === "mock" ? "" : new URL(url).hostname,
            credential_source: d.get("credential_source") || "encrypted",
            ...(protocol !== "mock" &&
            d.get("credential_source") !== "environment" &&
            d.get("api_key")
              ? { api_key: d.get("api_key") }
              : {}),
          };
      const success = await action.run(() =>
        request(
          selected ? `/admin/providers/${selected.id}` : "/admin/providers",
          selected ? "PUT" : "POST",
          payload,
        ),
      );
      if (success) {
        setSelected(null);
        form.reset();
      }
    } catch {
      await action.run(() =>
        Promise.reject(
          new ApiError(422, "validation_error", "Проверьте URL провайдера"),
        ),
      );
    } finally {
      clearKeys(form);
    }
  }
  return (
    <ListPanel title="Провайдеры AI">
      <p>
        Изменения действуют для новых задач. Адрес другого домена и другой
        протокол оформляются отдельным провайдером. Отключение сохраняет
        историю.
      </p>
      <Notice
        error={action.error || data.error || status.error}
        notice={action.notice}
      />
      {!status.data?.master_key_configured && (
        <p>
          Для сохранения ключей в БД требуется серверный AI_MASTER_KEY. Можно
          использовать уже подключённый ключ окружения.
        </p>
      )}
      <form key={selected?.id || "new"} onSubmit={(e) => void submit(e)}>
        <h3>{selected ? "Изменить провайдера" : "Добавить провайдера"}</h3>
        <label>
          Название провайдера
          <input
            name="name"
            required
            maxLength={100}
            defaultValue={selected?.name}
          />
        </label>
        {!selected && (
          <label>
            Протокол
            <select
              aria-label="Протокол"
              value={protocol}
              onChange={(e) => setProtocol(e.target.value)}
            >
              <option value="openai_chat">OpenAI Chat API</option>
              <option value="mock">Тестовый адаптер без сети</option>
            </select>
          </label>
        )}
        <label>
          URL API
          <input
            name="base_url"
            type="url"
            required={
              selected ? selected.protocol !== "mock" : protocol !== "mock"
            }
            defaultValue={selected?.base_url}
          />
        </label>
        {!selected && protocol !== "mock" && (
          <>
            <label>
              Источник ключа
              <select name="credential_source">
                <option value="encrypted">Ввести и зашифровать в БД</option>
                <option value="environment">Подключённый ключ окружения</option>
              </select>
            </label>
            <label>
              Новый API-ключ
              <input
                name="api_key"
                type="password"
                autoComplete="off"
                maxLength={2000}
              />
            </label>
          </>
        )}
        <button disabled={action.busy}>Сохранить провайдера</button>
        {selected && (
          <button type="button" onClick={() => setSelected(null)}>
            Отменить редактирование
          </button>
        )}
      </form>
      <ul>
        {data.data?.items.map((p) => (
          <li key={p.id}>
            <strong>{p.name}</strong> · {p.protocol} ·{" "}
            {p.has_key ? "Ключ подключён" : "Без ключа"}{" "}
            <button onClick={() => setSelected(p)}>Изменить {p.name}</button>
            <button
              disabled={action.busy}
              onClick={() =>
                void action.run(() =>
                  request(
                    `/admin/providers/${p.id}`,
                    p.enabled ? "DELETE" : "PATCH",
                    p.enabled ? undefined : { enabled: true },
                  ),
                )
              }
            >
              {p.enabled ? "Архивировать" : "Восстановить"} {p.name}
            </button>
            {p.protocol !== "mock" && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = e.currentTarget;
                  const key = new FormData(f).get("api_key");
                  void action
                    .run(() =>
                      request(`/admin/providers/${p.id}/credential`, "PUT", {
                        api_key: key,
                      }),
                    )
                    .finally(() => clearKeys(f));
                }}
              >
                <label>
                  Заменить ключ {p.name}
                  <input
                    name="api_key"
                    type="password"
                    required
                    autoComplete="off"
                    maxLength={2000}
                  />
                </label>
                <button
                  disabled={action.busy || !status.data?.master_key_configured}
                >
                  Сохранить ключ {p.name}
                </button>
              </form>
            )}
          </li>
        ))}
      </ul>
      <Pager
        offset={offset}
        total={data.data?.total || 0}
        onChange={setOffset}
      />
    </ListPanel>
  );
}

export function ModelsPanel() {
  const [offset, setOffset] = useState(0);
  const models = useResource<Page<Model>>(
    `/admin/models?offset=${offset}&limit=20`,
  );
  const providers = useChoices<Provider>("/admin/providers");
  const [selected, setSelected] = useState<Model | null>(null);
  const action = useAction();
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const d = new FormData(form);
    const price = d.get("input_price");
    if (
      (price !== "" || d.get("output_price") !== "") &&
      (price === "" || d.get("output_price") === "" || !d.get("currency"))
    ) {
      await action.run(() =>
        Promise.reject(
          new ApiError(
            422,
            "validation_error",
            "Заполните обе цены и валюту либо оставьте обе цены пустыми",
          ),
        ),
      );
      return;
    }
    const payload = {
      model_identifier: d.get("model_identifier"),
      capabilities: {
        supports_json_schema: true,
        max_context_tokens: Number(d.get("context")),
        mock_behavior: selected?.capabilities.mock_behavior || "success",
      },
      pricing:
        price !== ""
          ? {
              input_micros_per_million: Math.round(Number(price) * 1e6),
              output_micros_per_million: Math.round(
                Number(d.get("output_price")) * 1e6,
              ),
              currency: String(d.get("currency")).toUpperCase(),
            }
          : null,
      ...(!selected ? { provider_id: d.get("provider_id") } : {}),
    };
    if (
      await action.run(() =>
        request(
          selected ? `/admin/models/${selected.id}` : "/admin/models",
          selected ? "PUT" : "POST",
          payload,
        ),
      )
    ) {
      setSelected(null);
      form.reset();
    }
  }
  return (
    <ListPanel title="Модели AI">
      <Notice
        error={action.error || models.error || providers.error}
        notice={action.notice}
      />
      <form key={selected?.id || "new"} onSubmit={(e) => void submit(e)}>
        {!selected && (
          <label>
            Провайдер модели
            <select name="provider_id" required>
              {providers.data?.items
                .filter((p) => p.enabled)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
            </select>
          </label>
        )}
        <label>
          Идентификатор модели
          <input
            name="model_identifier"
            required
            maxLength={100}
            defaultValue={selected?.model_identifier}
          />
        </label>
        <label>
          Размер контекста, токены
          <input
            name="context"
            type="number"
            required
            min={512}
            max={1000000}
            defaultValue={selected?.capabilities.max_context_tokens || 4096}
          />
        </label>
        <p>
          Цена за миллион токенов. Пустые поля означают неизвестную стоимость.
        </p>
        <label>
          Цена входных токенов
          <input
            name="input_price"
            type="number"
            min="0"
            max="1000000"
            step="0.000001"
            defaultValue={
              selected?.pricing?.input_micros_per_million === undefined
                ? ""
                : selected.pricing.input_micros_per_million / 1e6
            }
          />
        </label>
        <label>
          Цена выходных токенов
          <input
            name="output_price"
            type="number"
            min="0"
            max="1000000"
            step="0.000001"
            defaultValue={
              selected?.pricing?.output_micros_per_million === undefined
                ? ""
                : selected.pricing.output_micros_per_million / 1e6
            }
          />
        </label>
        <label>
          Валюта тарифа
          <input
            name="currency"
            pattern="[A-Z]{3}"
            maxLength={3}
            defaultValue={selected?.pricing?.currency || "RUB"}
          />
        </label>
        <button disabled={action.busy}>Сохранить модель</button>
        {selected && (
          <button type="button" onClick={() => setSelected(null)}>
            Отменить редактирование
          </button>
        )}
      </form>
      <ul>
        {models.data?.items.map((m) => (
          <li key={m.id}>
            {m.model_identifier}{" "}
            <button onClick={() => setSelected(m)}>
              Изменить {m.model_identifier}
            </button>
            <button
              disabled={action.busy}
              onClick={() =>
                void action.run(() =>
                  request(
                    `/admin/models/${m.id}`,
                    m.enabled ? "DELETE" : "PATCH",
                    m.enabled ? undefined : { enabled: true },
                  ),
                )
              }
            >
              {m.enabled ? "Архивировать" : "Восстановить"} {m.model_identifier}
            </button>
          </li>
        ))}
      </ul>
      <Pager
        offset={offset}
        total={models.data?.total || 0}
        onChange={setOffset}
      />
    </ListPanel>
  );
}

interface Route {
  id: string;
  task_type: string;
  primary_model_id: string;
  fallback_model_id: string | null;
  config_version: number;
}
export function RoutesPanel() {
  const models = useChoices<Model>("/admin/models");
  const [offset, setOffset] = useState(0);
  const routes = useResource<Page<Route>>(
    `/admin/routes?offset=${offset}&limit=20`,
  );
  const action = useAction();
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const d = new FormData(e.currentTarget);
    await action.run(() =>
      request("/admin/routes", "POST", {
        task_type: d.get("task_type"),
        primary_model_id: d.get("primary_model_id"),
        fallback_model_id: d.get("fallback_model_id") || null,
        policy: {
          attempts_per_model: Number(d.get("attempts")),
          backoff_seconds: Number(d.get("backoff")),
          max_output_tokens: Number(d.get("tokens")),
          max_cost_micros:
            d.get("budget") === ""
              ? null
              : Math.round(Number(d.get("budget")) * 1e6),
          currency: d.get("budget") === "" ? null : d.get("currency"),
        },
      }),
    );
  }
  const options = models.data?.items
    .filter((m) => m.enabled)
    .map((m) => (
      <option key={m.id} value={m.id}>
        {m.model_identifier}
      </option>
    ));
  return (
    <ListPanel title="Маршруты AI">
      <p>
        Сохранение создаёт новую версию для новых задач. Уже запущенные задачи
        сохраняют прежние настройки.
      </p>
      <Notice
        error={action.error || models.error || routes.error}
        notice={action.notice}
      />
      <form onSubmit={(e) => void submit(e)}>
        <label>
          Задача маршрута
          <select name="task_type">
            {Object.entries(tasks).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Основная модель
          <select name="primary_model_id" required>
            {options}
          </select>
        </label>
        <label>
          Резервная модель
          <select name="fallback_model_id">
            <option value="">Без резервной модели</option>
            {options}
          </select>
        </label>
        <label>
          Попыток на модель
          <input
            name="attempts"
            type="number"
            min={1}
            max={2}
            defaultValue={1}
          />
        </label>
        <label>
          Задержка повтора, секунды
          <input
            name="backoff"
            type="number"
            min={1}
            max={30}
            defaultValue={2}
          />
        </label>
        <label>
          Максимум выходных токенов
          <input
            name="tokens"
            type="number"
            min={16}
            max={16384}
            defaultValue={128}
          />
        </label>
        <label>
          Бюджет одной задачи
          <input
            name="budget"
            type="number"
            min={0}
            max={1000}
            step="0.000001"
          />
        </label>
        <label>
          Валюта бюджета
          <input name="currency" pattern="[A-Z]{3}" defaultValue="RUB" />
        </label>
        <button disabled={action.busy}>Сохранить версию маршрута</button>
      </form>
      <ul>
        {routes.data?.items.map((r) => (
          <li key={r.id}>
            {tasks[r.task_type] || r.task_type} · версия {r.config_version} ·{" "}
            {models.data?.items.find((m) => m.id === r.primary_model_id)
              ?.model_identifier || r.primary_model_id}{" "}
            →{" "}
            {models.data?.items.find((m) => m.id === r.fallback_model_id)
              ?.model_identifier || "без резерва"}
          </li>
        ))}
      </ul>
      <Pager
        offset={offset}
        total={routes.data?.total || 0}
        onChange={setOffset}
      />
    </ListPanel>
  );
}

interface Override {
  user_id: string;
  metric: string;
  limit_value: number;
}
export function LimitsPanel() {
  const [offset, setOffset] = useState(0);
  const quotas = useResource<{
    defaults: Record<string, number>;
    overrides: Page<Override>;
  }>(`/admin/quotas?offset=${offset}&limit=20`);
  const action = useAction();
  return (
    <ListPanel title="Лимиты">
      <p>
        Пустой ID пользователя меняет общий лимит. Персональное исключение имеет
        приоритет. Снижение лимита не отменяет уже принятые задачи. Место
        учитывает файлы книг и аудио; общая книга учитывается в квоте каждой
        библиотеки.
      </p>
      <Notice error={action.error || quotas.error} notice={action.notice} />
      <dl>
        {Object.entries(quotas.data?.defaults || {}).map(([m, v]) => (
          <div key={m}>
            <dt>{metrics[m] || m}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const d = new FormData(e.currentTarget);
          void action.run(() =>
            request("/admin/quotas", "PUT", {
              metric: d.get("metric"),
              limit_value: Number(d.get("limit_value")),
              user_id: d.get("user_id") || null,
            }),
          );
        }}
      >
        <label>
          Метрика лимита
          <select name="metric">
            {Object.entries(metrics).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Значение лимита
          <input
            name="limit_value"
            type="number"
            required
            min={0}
            max={2000000000}
          />
        </label>
        <label>
          ID пользователя (необязательно)
          <input name="user_id" pattern="[0-9a-fA-F-]{36}" />
        </label>
        <button disabled={action.busy}>Сохранить лимит</button>
      </form>
      <ul>
        {quotas.data?.overrides.items.map((o) => (
          <li key={o.user_id + o.metric}>
            {o.user_id} · {metrics[o.metric] || o.metric} · {o.limit_value}{" "}
            <button
              disabled={action.busy}
              onClick={() =>
                void action.run(() =>
                  request(`/admin/quotas/${o.user_id}/${o.metric}`, "DELETE"),
                )
              }
            >
              Вернуть общий лимит {o.metric}
            </button>
          </li>
        ))}
      </ul>
      <Pager
        offset={offset}
        total={quotas.data?.overrides.total || 0}
        onChange={setOffset}
      />
    </ListPanel>
  );
}

interface Source {
  id: string;
  name: string;
  host: string;
  catalog_url: string;
  enabled: boolean;
  approvals: unknown[];
}
export function SourcesPanel() {
  const [offset, setOffset] = useState(0);
  const sources = useResource<Page<Source>>(
    `/admin/sources?offset=${offset}&limit=20`,
  );
  const [selected, setSelected] = useState<Source | null>(null);
  const action = useAction();
  return (
    <ListPanel title="Разрешённые источники">
      <p>
        Включение каталога не разрешает скачивать все книги. Для импорта нужны
        проверенные разрешения на конкретные издания.
      </p>
      <Notice error={action.error || sources.error} notice={action.notice} />
      <form
        key={selected?.id || "new"}
        onSubmit={(e) => {
          e.preventDefault();
          const form = e.currentTarget;
          const d = new FormData(form);
          void action.run(async () => {
            const url = String(d.get("catalog_url"));
            const payload = {
              name: d.get("name"),
              catalog_url: url,
              host: new URL(url).hostname,
              enabled: d.get("enabled") === "on",
              approvals: JSON.parse(String(d.get("approvals") || "[]")),
            };
            await request(
              selected ? `/admin/sources/${selected.id}` : "/admin/sources",
              selected ? "PUT" : "POST",
              payload,
            );
            setSelected(null);
            form.reset();
          });
        }}
      >
        <label>
          Название источника
          <input
            name="name"
            required
            maxLength={100}
            defaultValue={selected?.name}
          />
        </label>
        <label>
          URL каталога
          <input
            name="catalog_url"
            type="url"
            required
            defaultValue={selected?.catalog_url}
          />
        </label>
        <label>
          <input
            name="enabled"
            type="checkbox"
            defaultChecked={selected?.enabled}
          />
          Источник включён
        </label>
        <label>
          Разрешения на импорт (JSON)
          <textarea
            name="approvals"
            rows={6}
            defaultValue={JSON.stringify(selected?.approvals || [], null, 2)}
          />
        </label>
        <p>
          Для каждого разрешения: candidate_id, fingerprint, checksum,
          rights_status (cc0/public_domain/licensed), provenance с основанием
          использования.
        </p>
        <button disabled={action.busy}>Сохранить источник</button>
        {selected && (
          <button type="button" onClick={() => setSelected(null)}>
            Отменить редактирование
          </button>
        )}
      </form>
      <ul>
        {sources.data?.items.map((s) => (
          <li key={s.id}>
            {s.name} · {s.enabled ? "включён" : "отключён"} · разрешений:{" "}
            {s.approvals.length}{" "}
            <button onClick={() => setSelected(s)}>Изменить {s.name}</button>
            <button
              disabled={action.busy}
              onClick={() =>
                void action.run(() =>
                  request(`/admin/sources/${s.id}`, "PUT", {
                    name: s.name,
                    host: s.host,
                    catalog_url: s.catalog_url,
                    approvals: s.approvals,
                    enabled: !s.enabled,
                  }),
                )
              }
            >
              {s.enabled ? "Отключить" : "Включить"} {s.name}
            </button>
          </li>
        ))}
      </ul>
      <Pager
        offset={offset}
        total={sources.data?.total || 0}
        onChange={setOffset}
      />
    </ListPanel>
  );
}
