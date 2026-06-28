import { useEffect, useRef } from 'react'
import { useStore } from '../store/useStore'

export const useWebSocket = (url: string = 'ws://localhost:8000/ws') => {
  const socketRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<number | null>(null)
  
  const { 
    setConnected, 
    updateWalletMonitor, 
    addSignal, 
    setConfidenceGaugeScore, 
    setConfidenceHistory, 
    addTrade, 
    updateMetrics 
  } = useStore()

  const connect = () => {
    try {
      console.log('Connecting to WebSocket:', url)
      const ws = new WebSocket(url)
      socketRef.current = ws

      ws.onopen = () => {
        console.log('WebSocket Connected')
        setConnected(true)
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current)
          reconnectTimeoutRef.current = null
        }
      }

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data)
          console.log('Received WS message:', message)

          switch (message.type) {
            case 'initial_state': {
              const init = message.data
              if (init.signals) {
                init.signals.forEach((raw: any) => {
                  addSignal({
                    id: raw.signature || `sig_${Math.random()}`,
                    direction: raw.direction || 'HOLD',
                    token: raw.token_address ? `${raw.token_address.substring(0, 6)}...${raw.token_address.slice(-4)}` : 'Unknown',
                    confidence: raw.confidence_score ? Math.round(raw.confidence_score * 100) : 0,
                    timestamp: raw.timestamp ? new Date(raw.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString(),
                    details: `Source Wallet: ${raw.wallet_source ? raw.wallet_source.substring(0, 6) + '...' + raw.wallet_source.slice(-4) : 'N/A'}`
                  })
                })
              }
              if (init.stats) {
                updateMetrics({
                  winRate: init.stats.win_rate_pct !== null ? `${init.stats.win_rate_pct}%` : '--%',
                  triggersToday: String(init.stats.triggers_today || 0),
                  alertsFiredCount: String(init.stats.alerts_fired_24h || 0),
                  alertsFiredTotal: String(init.stats.total_signals_24h || 0)
                })
                updateWalletMonitor({
                  mlModel: init.stats.active_model_version || 'v0',
                  accuracy: init.stats.win_rate_pct !== null ? `${init.stats.win_rate_pct}%` : '--%',
                  triggerWindow: '5 min - OR'
                })
              }
              updateWalletMonitor({
                whaleA: { active: true, txCount: "12 tx/hr" },
                whaleB: { active: true, txCount: "8 tx/hr" }
              })
              break
            }
            case 'signal_new': {
              const raw = message.data
              const mappedSignal = {
                id: raw.signature || `sig_${Date.now()}`,
                direction: raw.direction || 'HOLD',
                token: raw.token_address ? `${raw.token_address.substring(0, 6)}...${raw.token_address.slice(-4)}` : 'Unknown',
                confidence: raw.confidence_score ? Math.round(raw.confidence_score * 100) : 0,
                timestamp: raw.timestamp ? new Date(raw.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString(),
                details: `Source Wallet: ${raw.wallet_source ? raw.wallet_source.substring(0, 6) + '...' + raw.wallet_source.slice(-4) : 'N/A'}`
              }
              addSignal(mappedSignal)
              setConfidenceGaugeScore(mappedSignal.confidence)
              break
            }
            case 'trade_opened': {
              const raw = message.data
              updateWalletMonitor({
                lastTrigger: `${new Date(raw.timestamp).toLocaleTimeString()} UTC`
              })
              break
            }
            case 'trade_closed': {
              const rawTrade = message.data
              const mappedTrade = {
                id: rawTrade.position_id || `tr_${Date.now()}`,
                direction: 'BUY' as const,
                token: rawTrade.token_address ? `${rawTrade.token_address.substring(0, 6)}...${rawTrade.token_address.slice(-4)}` : 'Unknown',
                pnl: `${rawTrade.pnl_pct_actual >= 0 ? '+' : ''}${Math.round(rawTrade.pnl_pct_actual * 100)}%`,
                isPositive: rawTrade.pnl_pct_actual >= 0
              }
              addTrade(mappedTrade)
              break
            }
            case 'wallet_monitor':
              updateWalletMonitor(message.data)
              break
            case 'signal':
              addSignal(message.data)
              if (message.data.confidence) {
                setConfidenceGaugeScore(message.data.confidence)
              }
              break
            case 'confidence_gauge':
              setConfidenceGaugeScore(message.data.score)
              break
            case 'confidence_history':
              setConfidenceHistory(message.data.points)
              break
            case 'trade':
              addTrade(message.data)
              break
            case 'metrics':
              updateMetrics(message.data)
              break
            default:
              console.log('Unhandled event type:', message.type)
          }
        } catch (err) {
          console.error('Error parsing WebSocket message:', err)
        }
      }

      ws.onclose = () => {
        console.log('WebSocket Disconnected. Attempting reconnect in 5s...')
        setConnected(false)
        scheduleReconnect()
      }

      ws.onerror = (error) => {
        console.error('WebSocket Error:', error)
        ws.close()
      }

    } catch (err) {
      console.error('Connection initialization failed:', err)
      scheduleReconnect()
    }
  }

  const scheduleReconnect = () => {
    if (reconnectTimeoutRef.current) return
    
    reconnectTimeoutRef.current = window.setTimeout(() => {
      reconnectTimeoutRef.current = null
      connect()
    }, 5000)
  }

  useEffect(() => {
    connect()

    return () => {
      if (socketRef.current) {
        socketRef.current.close()
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
    }
  }, [url])

  return socketRef.current
}
