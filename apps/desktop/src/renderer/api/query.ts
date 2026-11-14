import { QueryClient } from '@tanstack/react-query';
import { AppError } from '@tavo/shared';

export const queryClient = new QueryClient({ defaultOptions: { queries: {
  staleTime: 1000,
  retry: (failureCount, error) => failureCount < 1 && !(error instanceof AppError && ['UNAUTHENTICATED', 'FORBIDDEN', 'INVALID_DATA'].includes(error.code)),
  retryDelay: 1000,
}, mutations: { retry: false } } });
