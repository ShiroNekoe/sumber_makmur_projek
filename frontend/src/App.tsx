import Dashboard from './views/Dashboard'
import { useWebSocket } from './hooks/useWebSocket'

function App() {
  // Initialize the persistent WebSocket stream connection to backend
  useWebSocket()

  return (
    <div className="min-h-screen bg-cyber-bg text-gray-100 flex flex-col">
      <Dashboard />
    </div>
  )
}

export default App
