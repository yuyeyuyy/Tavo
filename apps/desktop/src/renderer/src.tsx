import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from './api/query';
import { Chat } from './chat/Chat';
import './styles.css';
import { initializeAppearance, ThemeSync } from './appearance';

initializeAppearance();
createRoot(document.getElementById('root')!).render(<StrictMode><ThemeSync/><QueryClientProvider client={queryClient}><Chat/></QueryClientProvider></StrictMode>);
