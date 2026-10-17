import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { SystemStatus } from './features/system/SystemStatus'

export default function App() {
  const [queryClient] = useState(() => new QueryClient())
  return (
    <QueryClientProvider client={queryClient}>
      <header className="topbar">
        <h1>Monitor de câmeras</h1>
      </header>
      <main className="content">
        <SystemStatus />
      </main>
    </QueryClientProvider>
  )
}
