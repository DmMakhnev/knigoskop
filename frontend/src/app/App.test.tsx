import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { HomePage } from '../pages/HomePage';
import { MemoryRouter } from 'react-router-dom';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test('shows the Russian landing page and live API status', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', service: 'knigoskop-api' }),
    }),
  );
  mount();
  expect(screen.getByRole('heading', { name: 'Книгоскоп' })).toBeVisible();
  expect(await screen.findByText('API доступен')).toBeVisible();
  expect(fetch).toHaveBeenCalledWith('/health');
});

test('reports connection failure', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  mount();
  expect(
    await screen.findByText('API недоступен. Проверьте запуск сервера.'),
  ).toBeVisible();
});

test('rejects an invalid successful response', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'broken' }),
    }),
  );
  mount();
  expect(
    await screen.findByText('API недоступен. Проверьте запуск сервера.'),
  ).toBeVisible();
});
