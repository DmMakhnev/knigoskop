import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { BookSearch } from './BookSearch';
const book = {
  title: 'Одинаковое название',
  author: 'Автор 1',
  edition_label: 'Редакция 1',
  translator: 'Переводчик 1',
  language: 'ru',
  format: 'txt',
  source_name: 'Каталог',
  provenance: 'Проверенный текст CC0',
  rights_status: 'cc0',
  can_import: true,
};
function response(data: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => data };
}
function mount() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <BookSearch />
    </QueryClientProvider>,
  );
}
function search() {
  fireEvent.change(screen.getByLabelText('Название или автор'), {
    target: { value: 'Одинаковое' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }));
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.cookie = 'knigoskop_csrf=; Max-Age=0; path=/';
});

test('local results appear before external completion; exact edition requires confirmation', async () => {
  let finish!: (value: unknown) => void;
  const external = new Promise((resolve) => {
    finish = resolve;
  });
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/csrf')) {
      document.cookie = 'knigoskop_csrf=x; path=/';
      return response({});
    }
    if (init?.method === 'POST') return response({});
    if (url.includes('scope=external')) return external;
    return response({
      items: [
        { ...book, text_version_id: 'version-1' },
        {
          ...book,
          author: 'Автор 2',
          edition_label: 'Редакция 2',
          text_version_id: 'version-2',
        },
      ],
      total: 2,
      source_errors: [],
    });
  });
  vi.stubGlobal('fetch', mock);
  mount();
  search();
  await screen.findByText(/Автор 2 · Редакция 2/);
  expect(mock.mock.calls.some(([url]) => url.includes('scope=external'))).toBe(
    true,
  );
  expect(mock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(
    false,
  );
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Выбрать издание' })[1],
  );
  const confirmation = screen.getByRole('region', {
    name: 'Проверьте выбранное издание',
  });
  expect(within(confirmation).getByText(/Автор 2 · Редакция 2/)).toBeTruthy();
  fireEvent.click(
    within(confirmation).getByRole('button', {
      name: 'Добавить выбранное издание',
    }),
  );
  await screen.findByText('Издание добавлено в мою библиотеку');
  expect(
    mock.mock.calls.find(([url]) => url.endsWith('/library'))?.[1]?.body,
  ).toBe('{"text_version_id":"version-2"}');
  finish(
    response({
      items: [],
      total: 0,
      source_errors: [
        { source_id: 'bad', message: 'Источник временно недоступен' },
      ],
    }),
  );
  await screen.findByText('Источник временно недоступен');
  expect(screen.getByText(/Автор 1 · Редакция 1/)).toBeTruthy();
});

test('unverified external candidate cannot be imported; approved selection sends only identifiers', async () => {
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/csrf')) {
      document.cookie = 'knigoskop_csrf=x; path=/';
      return response({});
    }
    if (init?.method === 'POST') return response({});
    return response({
      items: url.includes('scope=external')
        ? [
            {
              ...book,
              source_id: 'source',
              candidate_id: 'approved',
              fingerprint: 'hash',
            },
            {
              ...book,
              source_id: 'source',
              candidate_id: 'unknown',
              rights_status: 'unverified',
              can_import: false,
            },
          ]
        : [],
      total: 2,
      source_errors: [],
    });
  });
  vi.stubGlobal('fetch', mock);
  mount();
  search();
  const blocked = await screen.findByRole('button', {
    name: 'Права не подтверждены',
  });
  expect((blocked as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать издание' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Добавить выбранное издание' }),
  );
  await screen.findByText('Издание добавлено в мою библиотеку');
  expect(
    mock.mock.calls.find(([url]) => url.endsWith('/books/import'))?.[1]?.body,
  ).toBe(
    '{"source_id":"source","candidate_id":"approved","fingerprint":"hash"}',
  );
});

test('source error supports retry without removing local candidates', async () => {
  let failed = true;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      url.includes('scope=external') && failed
        ? response(
            { message: 'Временно недоступно', code: 'source_unavailable' },
            503,
          )
        : response({
            items: [{ ...book, text_version_id: 'one' }],
            total: 1,
            source_errors: [],
          }),
    ),
  );
  mount();
  search();
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: 'Выбрать издание' })).toBeTruthy();
  failed = false;
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
});
