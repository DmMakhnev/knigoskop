import { messages as translations } from '../app/messages';
import { Button } from '@radix-ui/themes';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getHealth } from '../api/health';
import { messages } from '../app/messages';

const t = translations.ru.auth;

export function HomePage() {
  const text = messages.ru;
  const health = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    retry: false,
  });
  return (
    <section>
      <p className="eyebrow">{t.personalSpace}</p>
      <h1>{text.title}</h1>
      <p className="slogan">{text.slogan}</p>
      <p>{text.description}</p>
      <p>
        <Link to="/register">{t.createAccount}</Link>
      </p>
      <section aria-label={t.connectionState} className="status-card">
        <p role="status">
          {health.isPending
            ? text.loading
            : health.isError
              ? text.unavailable
              : text.ready}
        </p>
        <Button
          onClick={() => void health.refetch()}
          disabled={health.isFetching}
        >
          {text.retry}
        </Button>
      </section>
    </section>
  );
}
