import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { errorMessage, request } from "../../api/auth";

interface Profile {
  id: string;
  name: string;
  ready: boolean;
  enabled: boolean;
  voices: string[];
  model: string;
}
export function AudioAdmin() {
  const profiles = useQuery({
    queryKey: ["admin-audio-profiles"],
    queryFn: () => request<{ items: Profile[] }>("/admin/audio/profiles"),
    retry: false,
  });
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
      const result = await request<{ items: { id: string; name: string }[] }>(
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
      });
      setName("");
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
        API-ключ задаётся на сервере через защищённые переменные окружения.
        Настройки модели GenVoice определяются голосом; дополнительные параметры
        модели API не принимает.
      </p>
      <button disabled={busy} onClick={() => void loadVoices()}>
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
        <button disabled={busy}>Сохранить профиль озвучивания</button>
      </form>
      <ul>
        {profiles.data?.items.map((p) => (
          <li key={p.id}>
            {p.name} · {p.ready ? "Ключ подключён" : "Ключ не подключён"} ·{" "}
            {p.voices.join(", ")}{" "}
            <button disabled={busy} onClick={() => void toggle(p)}>
              {p.enabled ? "Отключить" : "Включить"} {p.name}
            </button>
          </li>
        ))}
      </ul>
      {(error || profiles.error) && (
        <p role="alert">{error || errorMessage(profiles.error)}</p>
      )}
    </section>
  );
}
