import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { AIInfrastructure } from './AIInfrastructure';

function mount() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <AIInfrastructure />
    </QueryClientProvider>,
  );
}
function response(data: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => data };
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function api(configured = true, quota = false, active = false) {
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/csrf')) return response({});
    if (url.endsWith('/ai-status'))
      return response({
        route_configured: configured,
        master_key_configured: false,
      });
    if (url.includes('/admin/models?'))
      return response({
        items: [{ id: 'm1', model_identifier: 'offline-model', enabled: true }],
        total: 1,
      });
    if (url.endsWith('/diagnostic') || url.endsWith('/test'))
      return quota
        ? response(
            {
              code: 'quota_exceeded',
              message: 'Квота AI исчерпана; задача не запущена',
            },
            429,
          )
        : response({ id: 'j1' });
    if (url.endsWith('/cancel'))
      return response({ id: 'j1', status: 'cancelled' });
    if (url.endsWith('/jobs/j1')) {
      const cancelled = mock.mock.calls.some(([path]) =>
        String(path).endsWith('/cancel'),
      );
      return response({
        id: 'j1',
        status: cancelled ? 'cancelled' : active ? 'queued' : 'completed',
        progress: active ? 0 : 100,
        attempts: active ? 0 : 2,
        error_code: null,
      });
    }
    throw new Error(`Unexpected request method ${init?.method}`);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

test('keyless diagnostics explain the fixed request and disabled route', async () => {
  api(false);
  mount();
  expect(
    await screen.findByRole('button', { name: 'Проверить маршрут AI' }),
  ).toBeDisabled();
  expect(await screen.findByText(/Ключ шифрования не настроен/)).toBeVisible();
  expect(screen.getByText(/Тексты книг не передаются/)).toBeVisible();
});

test('route test posts an idempotency key and displays background result', async () => {
  const mock = api();
  mount();
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Проверить маршрут AI' }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Проверить маршрут AI' }));
  expect(
    await screen.findByText(/Завершена · 100% · Попытки: 2/),
  ).toBeVisible();
  const call = mock.mock.calls.find(([path]) => path.endsWith('/diagnostic'));
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({
    idempotency_key: expect.any(String),
  });
});

test('model test reports a quota rejection without creating a polling job', async () => {
  const mock = api(true, true);
  mount();
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Тест соединения offline-model',
    }),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Квота AI исчерпана',
  );
  expect(mock.mock.calls.some(([path]) => path.endsWith('/jobs/j1'))).toBe(
    false,
  );
});

test('queued diagnostic can be cancelled', async () => {
  api(true, false, true);
  mount();
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Проверить маршрут AI' }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Проверить маршрут AI' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Отменить задачу' }),
  );
  expect(await screen.findByText(/Отменена/)).toBeVisible();
});

test('retry after a lost response reuses the original idempotency key', async () => {
  const mock = api();
  const original = mock.getMockImplementation()!;
  let lost = true;
  mock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/diagnostic') && lost) {
      lost = false;
      throw new Error('Connection lost after server accepted job');
    }
    return original(url, init);
  });
  mount();
  const button = screen.getByRole('button', { name: 'Проверить маршрут AI' });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(await screen.findByRole('alert')).toHaveTextContent('Нет связи');
  fireEvent.click(button);
  expect(await screen.findByText(/Завершена/)).toBeVisible();
  const calls = mock.mock.calls.filter(([url]) => url.endsWith('/diagnostic'));
  expect(calls).toHaveLength(2);
  expect(calls[0][1]?.body).toEqual(calls[1][1]?.body);
});
