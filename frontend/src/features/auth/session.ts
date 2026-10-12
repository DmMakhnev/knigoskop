import { useQuery } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import type { User } from '../../api/auth';
import { getMe } from '../../api/auth';

export function useSession() {
  return useQuery({
    queryKey: ['me'],
    queryFn: getMe,
    retry: false,
    staleTime: 0,
  });
}

export async function replaceSession(client: QueryClient, user: User | null) {
  await client.cancelQueries();
  client.removeQueries({ predicate: (query) => query.queryKey[0] !== 'me' });
  client.setQueryData(['me'], user);
}
