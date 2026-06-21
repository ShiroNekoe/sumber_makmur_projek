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
