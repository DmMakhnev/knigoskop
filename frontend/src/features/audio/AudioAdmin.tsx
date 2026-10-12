import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { errorMessage, request } from "../../api/auth";

interface Profile {
  id: string;
  name: string;
  ready: boolean;
  enabled: boolean;
  protocol: string;
  voices: string[];
  model: string;
}
export function AudioAdmin() {
  const profiles = useQuery({
    queryKey: ["admin-audio-profiles"],
    queryFn: () => request<{ items: Profile[] }>("/admin/audio/profiles"),
    retry: false,
  });
  const security = useQuery({
    queryKey: ["admin-ai-status"],
    queryFn: () =>
      request<{ master_key_configured: boolean }>("/admin/ai-status"),
    retry: false,
  });
  const routes = useQuery({
    queryKey: ["admin-audio-routes"],
    queryFn: () =>
      request<{
        items: {
          id: string;
          primary_profile_id: string;
          fallback_profile_id: string | null;
          version: number;
          fallback_policy: string;
        }[];
      }>("/admin/audio/routes"),
    retry: false,
  });
  const [apiKey, setApiKey] = useState("");
  const [protocol, setProtocol] = useState("genvoice");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [voices, setVoices] = useState("");
  const [price, setPrice] = useState("");
  const [currency, setCurrency] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [catalog, setCatalog] = useState<{ id: string; name: string }[]>([]);
  async function loadVoices() {
    setBusy(true);
    setError("");
    try {
      const result = apiKey
        ? await request<{ items: { id: string; name: string }[] }>(
            "/admin/audio/voices/genvoice",
            "POST",
            { api_key: apiKey },
          )
        : await request<{ items: { id: string; name: string }[] }>(
            "/admin/audio/voices/genvoice",
          );
      setCatalog(result.items);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await request("/admin/audio/profiles", "POST", {
        name,
        protocol,
        base_url: url,
        host: new URL(url).hostname,
        model: "provider-default",
        voices: voices
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        price_micros_per_1000_chars: Math.round(Number(price) * 1_000_000),
        currency,
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setName("");
      setApiKey("");
      await profiles.refetch();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  async function toggle(profile: Profile) {
    setBusy(true);
    try {
      await request(`/admin/audio/profiles/${profile.id}`, "PATCH", {
        enabled: !profile.enabled,
      });
      await profiles.refetch();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="Настройки озвучивания">
      <h2>Настройки озвучивания</h2>
      <p>
        API-ключ можно подключить из окружения или сохранить зашифрованным в БД.
        Для ввода через админку требуется серверный AI_MASTER_KEY. Настройки
        модели GenVoice определяются голосом; дополнительные параметры модели
        API не принимает.
      </p>
      <button
        disabled={busy || protocol !== "genvoice"}
        onClick={() => void loadVoices()}
      >
        Получить список голосов GenVoice
      </button>
      {catalog.length > 0 && (
        <ul aria-label="Голоса GenVoice">
          {catalog.map((v) => (
            <li key={v.id}>
              <button onClick={() => setVoices(v.id)}>{v.name}</button> · {v.id}
            </li>
          ))}
        </ul>
      )}
      <label>
        API-ключ TTS
        <input
          type="password"
          autoComplete="off"
          maxLength={2000}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
      </label>
      {apiKey && !security.data?.master_key_configured && (
        <p role="alert">
          Ключ шифрования не настроен: сохранить API-ключ в БД сейчас нельзя.
        </p>
      )}
      <form onSubmit={(e) => void create(e)}>
        <label>
          Серверный провайдер{" "}
          <select
            value={protocol}
            onChange={(e) => setProtocol(e.target.value)}
          >
            <option value="genvoice">GenVoice</option>
            <option value="yandex_speechkit">Yandex SpeechKit v1</option>
          </select>
        </label>
        <label>
          Название профиля{" "}
          <input
            required
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          Базовый URL провайдера{" "}
          <input
            required
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </label>
        <label>
          Идентификаторы голосов через запятую{" "}
          <input
            required
            value={voices}
            onChange={(e) => setVoices(e.target.value)}
          />
        </label>
        <label>
          Цена за 1000 символов{" "}
          <input
            required
            type="number"
            min="0"
            max="1000"
            step="0.01"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
          />
        </label>
        <label>
          Валюта цены{" "}
          <input
            required
            pattern="[A-Z]{3}"
            maxLength={3}
            value={currency}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          />
        </label>
        <button
          disabled={busy || (!!apiKey && !security.data?.master_key_configured)}
        >
          Сохранить профиль озвучивания
        </button>
      </form>
      <ul>
        {profiles.data?.items.map((p) => (
          <li key={p.id}>
            {p.name} · {p.ready ? "Ключ подключён" : "Ключ не подключён"} ·{" "}
            {p.voices.join(", ")}{" "}
            <button disabled={busy} onClick={() => void toggle(p)}>
              {p.enabled ? "Отключить" : "Включить"} {p.name}
            </button>
            {p.protocol !== "mock" && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const form = e.currentTarget;
                  const key = new FormData(form).get("api_key");
                  setBusy(true);
                  setError("");
                  void request(
                    `/admin/audio/profiles/${p.id}/credential`,
                    "PUT",
                    { api_key: key },
                  )
                    .then(() => profiles.refetch())
                    .catch((e) => setError(errorMessage(e)))
                    .finally(() => {
                      form.reset();
                      setBusy(false);
                    });
                }}
              >
                <label>
                  Заменить ключ TTS {p.name}
                  <input
                    name="api_key"
                    type="password"
                    autoComplete="off"
                    required
                    maxLength={2000}
                  />
                </label>
                <button
                  disabled={busy || !security.data?.master_key_configured}
                >
                  Сохранить ключ TTS {p.name}
                </button>
              </form>
            )}
          </li>
        ))}
      </ul>
      <h3>Маршрут TTS</h3>
      <p>
        Основной профиль предлагается по умолчанию. Резервный выбирается
        вручную; после неизвестного списания автоматический платный повтор
        запрещён. Web Speech остаётся браузерным fallback.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const d = new FormData(e.currentTarget);
          setBusy(true);
          setError("");
          void request("/admin/audio/routes", "POST", {
            primary_profile_id: d.get("primary"),
            fallback_profile_id: d.get("fallback") || null,
          })
            .then(() => routes.refetch())
            .catch((e) => setError(errorMessage(e)))
            .finally(() => setBusy(false));
        }}
      >
        <label>
          Основной профиль TTS
          <select name="primary" required>
            {profiles.data?.items
              .filter((p) => p.enabled)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          Резервный профиль TTS
          <select name="fallback">
            <option value="">Без серверного резерва</option>
            {profiles.data?.items
              .filter((p) => p.enabled)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </select>
        </label>
        <button disabled={busy}>Сохранить маршрут TTS</button>
      </form>
      <ul>
        {routes.data?.items.map((r) => (
          <li key={r.id}>
            Версия {r.version} ·{" "}
            {profiles.data?.items.find((p) => p.id === r.primary_profile_id)
              ?.name || r.primary_profile_id}{" "}
            →{" "}
            {profiles.data?.items.find((p) => p.id === r.fallback_profile_id)
              ?.name || "Web Speech"}
          </li>
        ))}
      </ul>
      {routes.error && <p role="alert">{errorMessage(routes.error)}</p>}
      {(error || profiles.error) && (
        <p role="alert">{error || errorMessage(profiles.error)}</p>
      )}
    </section>
  );
}
