import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { AuthGate } from './features/auth/AuthGate'
import { SystemStatus } from './features/system/SystemStatus'

export default function App() {
  const [queryClient] = useState(() => new QueryClient())
  return (
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        <SystemStatus />
      </AuthGate>
    </QueryClientProvider>
  )
}
