import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, test, vi } from 'vitest';
import { ReaderSummary } from './ReaderSummary';
import { PromptAdmin } from './PromptAdmin';

const response = (body: unknown, status = 200) => ({
  ok: status < 400,
  status,
  json: async () => body,
});
function mount(component: React.ReactNode) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {component}
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function summaryApi(external = false, loseReply = false) {
  let launches = 0;
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/csrf')) return response({});
    if (url.endsWith('/processing-config'))
      return response({
        ready: true,
        external,
        models: ['provider: model'],
        latest_job_id: null,
      });
    if (url.endsWith('/process') && init?.method === 'POST') {
      launches++;
      if (loseReply && launches === 1) throw new TypeError('Network');
      return response({ id: 'job', status: 'completed' });
    }
    if (url.includes('/jobs/'))
      return response({ id: 'job', status: 'completed', progress: 100 });
    if (url.includes('/nodes'))
      return response({
        items: [
          {
            id: 'node',
            parent_id: null,
            kind: 'root',
            title: 'Глава 1',
            text: '<script>text</script>',
            source_spans: [
              {
                text_version_id: 'v1',
                section_id: 'chapter',
                start_offset: 1,
                end_offset: 3,
              },
            ],
            provenance: {
              generation: { model_identifier: 'model', prompt_version: 1 },
            },
          },
        ],
        total: 1,
      });
    if (url.includes('/validations')) return response({ items: [], total: 0 });
    return response({
      items: [
        {
          id: 'summary',
          generation_id: 'gen',
          text_version_id: 'v1',
          created_at: '2026-10-11T10:00:00Z',
          provenance: { mock: true },
        },
      ],
      total: 1,
    });
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

test('opens exact same-version source and renders summary safely', async () => {
  summaryApi();
  const onSource = vi.fn();
  mount(<ReaderSummary itemId="item" versionId="v1" onSource={onSource} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Открыть в оригинале' }),
  );
  expect(onSource).toHaveBeenCalledWith('chapter', 1, 3);
  expect(document.querySelector('script')).toBeNull();
  expect(screen.getAllByText(/локальный mock/).length).toBeGreaterThan(0);
});

test('requires consent before external book processing', async () => {
  summaryApi(true);
  mount(<ReaderSummary itemId="item" versionId="v1" onSource={vi.fn()} />);
  const consent = await screen.findByRole('checkbox');
  const button = screen.getByRole('button', { name: 'Обработать книгу' });
  expect(button).toBeDisabled();
  fireEvent.click(consent);
  expect(button).toBeEnabled();
});

test('never opens a foreign-version source', async () => {
  summaryApi();
  mount(<ReaderSummary itemId="item" versionId="other" onSource={vi.fn()} />);
  await screen.findByText('<script>text</script>');
  expect(
    screen.queryByRole('button', { name: 'Открыть в оригинале' }),
  ).toBeNull();
});

test('lost launch reply reuses the idempotency key', async () => {
  const mock = summaryApi(false, true);
  mount(<ReaderSummary itemId="item" versionId="v1" onSource={vi.fn()} />);
  const button = screen.getByRole('button', { name: 'Обработать книгу' });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await screen.findByRole('alert');
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await screen.findByRole('status');
  const launches = mock.mock.calls.filter(([url]) => url.endsWith('/process'));
  expect(launches).toHaveLength(2);
  expect(JSON.parse(launches[0][1]!.body as string).idempotency_key).toBe(
    JSON.parse(launches[1][1]!.body as string).idempotency_key,
  );
});

test('administrator publishes the selected immutable prompt version', async () => {
  const mock = vi.fn(async (url: string) =>
    url.includes('/versions')
      ? response({
          items: [
            {
              id: 'draft',
              version: 2,
              body: '$input',
              variables_schema: {},
              status: 'draft',
            },
          ],
          total: 1,
        })
      : url.includes('/admin/prompts')
        ? response({
            items: [
              {
                id: 'template',
                task_type: 'structure',
                name: 'Структура',
                active_version_id: null,
              },
            ],
          })
        : response({}),
  );
  vi.stubGlobal('fetch', mock);
  mount(<PromptAdmin />);
  const button = await screen.findByRole('button', {
    name: 'Опубликовать версию',
  });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await waitFor(() =>
    expect(
      mock.mock.calls.some(([url]) => url.endsWith('/draft/publish')),
    ).toBe(true),
  );
});
