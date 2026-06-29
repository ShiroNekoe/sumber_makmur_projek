import { useEffect, useRef } from 'react'
import { useStore } from '../store/useStore'

export const useWebSocket = (url: string = 'ws://localhost:8000/ws') => {
  const socketRef = useRef<WebSocket | null>(null)
  const reconnectDelayRef = useRef<number>(1000) // Initial delay: 1s
  const reconnectTimeoutRef = useRef<number | null>(null)
  const pingIntervalRef = useRef<number | null>(null)
  
  const { 
    setConnected, 
    updateWalletMonitor, 
    addSignal, 
    setConfidenceGaugeScore, 
    setConfidenceHistory, 
    addTrade, 
    updateMetrics,
    setSystemStatus,
    addWalletCandidate,
    approveWalletCandidate,
    addNotification,
  } = useStore()

  const connect = () => {
    try {
      console.log('Connecting to WebSocket:', url)
      const ws = new WebSocket(url)
      socketRef.current = ws

      ws.onopen = () => {
        console.log('WebSocket Connected')
        setConnected(true)
        reconnectDelayRef.current = 1000 // Reset backoff delay on successful connection
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current)
          reconnectTimeoutRef.current = null
        }
        
        // Start ping heartbeat every 30 seconds
        startPingInterval()
      }

      ws.onmessage = (event) => {
        try {
          const envelope = JSON.parse(event.data)
          console.log('Received WS message:', envelope)

          const payload = envelope.data
          const type = envelope.type

          switch (type) {
            case 'initial_state': {
              const init = payload
              // 1. Load historical signals
              if (init.signals) {
                const mappedSignals = init.signals.map((raw: any) => ({
                  id: raw.signal_id || raw.signature || `sig_${Math.random()}`,
                  direction: raw.direction || 'HOLD',
                  token: raw.token_address ? `${raw.token_address.substring(0, 6)}...${raw.token_address.slice(-4)}` : 'Unknown',
                  confidence: raw.confidence_score ? Math.round(raw.confidence_score * 100) : 0,
                  timestamp: raw.timestamp ? new Date(raw.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString(),
                  details: `Source Whale: ${raw.wallet_source ? raw.wallet_source.substring(0, 6) + '...' + raw.wallet_source.slice(-4) : 'N/A'}`,
                  safetyPassed: raw.safety_passed || false
                }))
                
                // Set the initial signals and build confidence chart data
                const historyPoints = mappedSignals.map((s: any) => ({
                  id: s.id,
                  timestamp: s.timestamp,
                  score: s.confidence
                })).reverse()
                
                setConfidenceHistory(historyPoints)
                // Add signals to list
                mappedSignals.forEach((s: any) => addSignal(s))
              }
              // 2. Load metrics & system stats
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
              if (init.system_status) {
                setSystemStatus(init.system_status)
                updateWalletMonitor({
                  whaleA: { active: init.system_status.rpc_status !== 'offline', txCount: "12 tx/hr" },
                  whaleB: { active: init.system_status.rpc_status !== 'offline', txCount: "8 tx/hr" }
                })
              }
              break
            }
            case 'signal_new': {
              const raw = payload
              const isAlert = raw.event === 'ALERT'
              const confidenceVal = raw.confidence_score ? Math.round(raw.confidence_score * 100) : 0
              
              const mappedSignal = {
                id: raw.signal_id || raw.signature || `sig_${Date.now()}`,
                direction: raw.direction || 'HOLD',
                token: raw.token_address ? `${raw.token_address.substring(0, 6)}...${raw.token_address.slice(-4)}` : 'Unknown',
                confidence: confidenceVal,
                timestamp: raw.timestamp ? new Date(raw.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString(),
                details: `Source Whale: ${raw.wallet_source ? raw.wallet_source.substring(0, 6) + '...' + raw.wallet_source.slice(-4) : 'N/A'}`,
                safetyPassed: raw.safety_passed || false
              }
              
              addSignal(mappedSignal)
              setConfidenceGaugeScore(mappedSignal.confidence)
              
              if (isAlert) {
                addNotification(`🚨 BUY SIGNAL DETECTED: Token ${mappedSignal.token} at ${mappedSignal.confidence}% confidence!`, 'warning')
              }
              break
            }
            case 'trade_opened': {
              const raw = payload
              updateWalletMonitor({
                lastTrigger: `${new Date(raw.timestamp).toLocaleTimeString()} UTC`
              })
              const shortToken = raw.token_address ? `${raw.token_address.substring(0, 6)}...${raw.token_address.slice(-4)}` : 'Unknown'
              addNotification(`💸 AUTO-ENTRY: Position opened for ${shortToken} ($${raw.position_size_usd.toFixed(2)})!`, 'success')
              break
            }
            case 'trade_closed': {
              const raw = payload
              const pnlPct = Math.round(raw.pnl_pct_actual * 100)
              const isWin = raw.pnl_pct_actual >= 0
              const shortToken = raw.token_address ? `${raw.token_address.substring(0, 6)}...${raw.token_address.slice(-4)}` : 'Unknown'

              const mappedTrade = {
                id: raw.position_id || `tr_${Date.now()}`,
                direction: 'BUY' as const,
                token: shortToken,
                pnl: `${isWin ? '+' : ''}${pnlPct}% (${raw.exit_reason})`,
                isPositive: isWin
              }
              addTrade(mappedTrade)
              
              addNotification(`🔒 POSITION CLOSED: ${shortToken} exited via ${raw.exit_reason.toUpperCase()} PnL: ${isWin ? '+' : ''}${pnlPct}%`, isWin ? 'success' : 'error')
              break
            }
            case 'system_status': {
              setSystemStatus(payload)
              break
            }
            case 'wallet_candidate': {
              const raw = payload
              const cand = {
                wallet_address: raw.wallet_address,
                wallet_short: raw.wallet_short || `${raw.wallet_address.substring(0, 6)}...${raw.wallet_address.slice(-4)}`,
                label: raw.label || 'Auto-Discovered',
                source: 'auto_discovered',
                discovery_reason: raw.discovery_reason || 'Consistent Profit Correlation',
                discovered_at: raw.discovered_at || new Date().toISOString(),
                status: 'pending' as const
              }
              addWalletCandidate(cand)
              addNotification(`🔍 DISCOVERED CANDIDATE: New smart money wallet candidate found: ${cand.wallet_short}`, 'info')
              break
            }
            case 'wallet_approval_result': {
              const raw = payload
              approveWalletCandidate(raw.wallet_address, raw.action)
              addNotification(`✅ WALLET ACTION: Candidate ${raw.wallet_short} has been ${raw.action}d.`, 'info')
              break
            }
            case 'rollback_alert': {
              const raw = payload
              addNotification(`⚠️ ROLLBACK GUARD: New model ${raw.model_version} was rejected due to drop in validation score. Restored previous version.`, 'error')
              break
            }
            case 'model_updated': {
              addNotification(`🤖 MODEL RELOAD: ML engine hot-reloaded a new model version successfully!`, 'success')
              break
            }
            case 'system_alert': {
              const raw = payload
              const level = raw.alert_type === 'rpc_degraded' ? 'error' : (raw.alert_type === 'rpc_failover' ? 'warning' : 'success')
              addNotification(`🔌 SYSTEM ALERT: ${raw.message}`, level)
              break
            }
            case 'position_cap_reached': {
              const raw = payload
              addNotification(`ℹ️ POSITION CAP: Correlation cap reached (${raw.open_count}/${raw.max_count}). Trade blocked.`, 'info')
              break
            }
            case 'ping_ack': {
              // Received pong from server, heartbeat healthy
              break
            }
            default:
              console.log('Unhandled event type:', type)
          }
        } catch (err) {
          console.error('Error parsing WebSocket message:', err)
        }
      }

      ws.onclose = () => {
        console.log('WebSocket Disconnected. Attempting reconnect...')
        setConnected(false)
        stopPingInterval()
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
    
    const delay = reconnectDelayRef.current
    console.log(`Reconnecting in ${delay}ms...`)
    
    reconnectTimeoutRef.current = window.setTimeout(() => {
      reconnectTimeoutRef.current = null
      
      // Implement Exponential Backoff with Max 30s Cap
      reconnectDelayRef.current = Math.min(reconnectDelayRef.current * 2, 30000)
      connect()
    }, delay)
  }

  const startPingInterval = () => {
    if (pingIntervalRef.current) return
    pingIntervalRef.current = window.setInterval(() => {
      if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify({ type: 'ping', timestamp: new Date().toISOString() }))
      }
    }, 30000) // 30s interval
  }

  const stopPingInterval = () => {
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current)
      pingIntervalRef.current = null
    }
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
      stopPingInterval()
    }
  }, [url])

  return socketRef.current
}
