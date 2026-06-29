import React, { useEffect } from 'react'
import { 
  Activity, 
  Wifi, 
  WifiOff, 
  TrendingUp, 
  Gauge, 
  Clock, 
  ListTodo, 
  RotateCw, 
  Compass, 
  Cpu,
  X,
  Check,
  Server,
  Zap,
  Layers,
  ListFilter,
  History,
  AlertOctagon,
  Eye
} from 'lucide-react'
import { useStore } from '../store/useStore'
import { 
  approveWallet, 
  triggerManualRetrain, 
  fetchWalletCandidates, 
  fetchSystemStatus, 
  fetchRecentTrades, 
  fetchRecentSignals, 
  fetchSystemErrors 
} from '../services/api'

export const Dashboard: React.FC = () => {
  const { 
    isConnected, 
    walletMonitor, 
    liveSignals, 
    confidenceGaugeScore, 
    confidenceThreshold, 
    tradeLog, 
    metrics,
    systemStatus,
    walletCandidates,
    notifications,
    activeTab,
    selectedSignal,
    selectedTrade,
    selectedError,
    errorLogs,
    setWalletCandidates,
    setSystemStatus,
    approveWalletCandidate,
    addNotification,
    dismissNotification,
    setActiveTab,
    setSelectedSignal,
    setSelectedTrade,
    setSelectedError,
    setErrorLogs,
    addSignal,
    addTrade
  } = useStore()

  // Load and refresh tab data
  useEffect(() => {
    const loadInitialData = async () => {
      try {
        const cands = await fetchWalletCandidates()
        if (cands && cands.candidates) {
          setWalletCandidates(cands.candidates)
        }
        const status = await fetchSystemStatus()
        if (status) {
          setSystemStatus(status)
        }
      } catch (err) {
        console.warn("Failed fetching initial dashboard API data:", err)
      }
    }
    loadInitialData()
  }, [])

  // Poll diagnostics or lists when activeTab changes
  useEffect(() => {
    const fetchTabData = async () => {
      try {
        if (activeTab === 'diagnostics') {
          const errorsRes = await fetchSystemErrors(50)
          if (errorsRes && errorsRes.errors) {
            setErrorLogs(errorsRes.errors)
          }
        } else if (activeTab === 'trades') {
          const tradesRes = await fetchRecentTrades(50)
          if (tradesRes && tradesRes.trades) {
            // Populate store trade log from API
            tradesRes.trades.forEach((t: any) => {
              addTrade({
                id: t.trade_id,
                direction: t.direction,
                token: t.token_symbol || t.token_address.substring(0, 6),
                pnl: `${t.pnl_pct_actual >= 0 ? '+' : ''}${(t.pnl_pct_actual * 100).toFixed(1)}%`,
                isPositive: t.pnl_pct_actual >= 0
              })
            })
          }
        } else if (activeTab === 'signals') {
          const sigsRes = await fetchRecentSignals(48)
          if (sigsRes && sigsRes.signals) {
            sigsRes.signals.forEach((s: any) => {
              addSignal({
                id: s.signal_id,
                direction: s.direction,
                token: s.token_short,
                confidence: Math.round(s.confidence_score * 100),
                timestamp: new Date(s.timestamp).toLocaleTimeString(),
                details: `Wallet: ${s.wallet_short}`,
                safetyPassed: s.safety_passed
              })
            })
          }
        }
      } catch (err) {
        console.warn(`Failed loading data for tab ${activeTab}:`, err)
      }
    }
    fetchTabData()
  }, [activeTab])

  const handleWalletAction = async (address: string, action: 'approve' | 'reject') => {
    try {
      addNotification(`Sending ${action} request for ${address.substring(0, 6)}...`, 'info')
      const res = await approveWallet(address, action)
      if (res.success) {
        approveWalletCandidate(address, action)
        addNotification(`Wallet candidate successfully ${action}d`, 'success')
      }
    } catch (err) {
      console.error(err)
      addNotification(`Failed to ${action} candidate wallet`, 'error')
    }
  }

  const handleRetrain = async () => {
    try {
      addNotification("Triggering manual XGBoost retrain pipeline...", 'info')
      const res = await triggerManualRetrain()
      if (res.status === 'success') {
        addNotification("Manual training completed successfully! New model activated.", 'success')
      } else {
        addNotification(`Manual retrain finished: ${res.message}`, 'warning')
      }
      const status = await fetchSystemStatus()
      if (status) setSystemStatus(status)
    } catch (err) {
      console.error(err)
      addNotification("Retraining failed. Check backend logs.", 'error')
    }
  }

  return (
    <div className="flex-1 flex flex-col p-4 md:p-6 bg-cyber-bg min-h-screen relative text-gray-100 font-sans">
      
      {/* Toast Notifications Overlay */}
      <div className="fixed top-4 right-4 z-50 flex flex-col space-y-2 w-full max-w-sm">
        {notifications.map((toast) => (
          <div 
            key={toast.id} 
            className={`p-3 rounded-lg border flex items-start justify-between shadow-lg transition-all duration-300 ${
              toast.type === 'success' ? 'bg-cyber-card border-cyber-emerald/50 text-cyber-emerald' :
              toast.type === 'error' ? 'bg-cyber-card border-cyber-rose/50 text-cyber-rose' :
              toast.type === 'warning' ? 'bg-cyber-card border-cyber-amber/50 text-cyber-amber' :
              'bg-cyber-card border-cyber-border text-white'
            }`}
          >
            <div className="flex items-start space-x-2">
              <span className="text-sm font-mono mt-0.5">
                {toast.type === 'success' ? '✓' : toast.type === 'error' ? '✗' : '!'}
              </span>
              <div>
                <p className="text-xs font-semibold leading-tight font-mono">{toast.message}</p>
                <span className="text-[9px] opacity-60 font-mono">{toast.timestamp}</span>
              </div>
            </div>
            <button 
              onClick={() => dismissNotification(toast.id)} 
              className="text-cyber-textMuted hover:text-white p-0.5 rounded transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>

      {/* Header Panel */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center pb-4 mb-6 border-b border-cyber-border/60 gap-4">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-indigo-600/10 rounded-lg border border-indigo-500/20 text-indigo-400">
            <Cpu className="w-6 h-6 animate-pulse" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-wider text-white">SUMBER MAKMUR SYSTEM</h1>
            <p className="text-xs text-cyber-textMuted font-mono">5-LAYER TRANSACTION ANALYSIS ENGINE</p>
          </div>
        </div>

        {/* Tab Selection Navigation */}
        <nav className="flex bg-cyber-cardLight/60 p-1 rounded-lg border border-cyber-border/40 font-mono text-xs">
          <button 
            onClick={() => setActiveTab('overview')} 
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded transition-all cursor-pointer ${activeTab === 'overview' ? 'bg-indigo-600 text-white shadow' : 'text-cyber-textMuted hover:text-white'}`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>OVERVIEW</span>
          </button>
          <button 
            onClick={() => setActiveTab('watchlist')} 
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded transition-all cursor-pointer ${activeTab === 'watchlist' ? 'bg-indigo-600 text-white shadow' : 'text-cyber-textMuted hover:text-white'}`}
          >
            <ListFilter className="w-3.5 h-3.5" />
            <span>WATCHLIST</span>
          </button>
          <button 
            onClick={() => setActiveTab('signals')} 
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded transition-all cursor-pointer ${activeTab === 'signals' ? 'bg-indigo-600 text-white shadow' : 'text-cyber-textMuted hover:text-white'}`}
          >
            <Activity className="w-3.5 h-3.5" />
            <span>SIGNALS</span>
          </button>
          <button 
            onClick={() => setActiveTab('trades')} 
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded transition-all cursor-pointer ${activeTab === 'trades' ? 'bg-indigo-600 text-white shadow' : 'text-cyber-textMuted hover:text-white'}`}
          >
            <History className="w-3.5 h-3.5" />
            <span>TRADES</span>
          </button>
          <button 
            onClick={() => setActiveTab('diagnostics')} 
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded transition-all cursor-pointer ${activeTab === 'diagnostics' ? 'bg-indigo-600 text-white shadow' : 'text-cyber-textMuted hover:text-white'}`}
          >
            <AlertOctagon className="w-3.5 h-3.5" />
            <span>DIAGNOSTICS</span>
          </button>
        </nav>

        {/* Action Controls & WebSocket status */}
        <div className="flex items-center space-x-4">
          <button 
            onClick={handleRetrain}
            className="flex items-center space-x-1 px-3 py-1.5 rounded-lg border border-indigo-500/30 bg-indigo-600/10 text-xs font-mono font-semibold text-indigo-400 hover:bg-indigo-600 hover:text-white transition-all cursor-pointer"
          >
            <RotateCw className="w-3.5 h-3.5" />
            <span>TRIGGER RETRAIN</span>
          </button>

          {isConnected ? (
            <div className="flex items-center space-x-2 bg-cyber-emerald/10 border border-cyber-emerald/20 px-3 py-1 rounded-full text-xs text-cyber-emerald font-mono">
              <Wifi className="w-3.5 h-3.5" />
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyber-emerald opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-cyber-emerald"></span>
              </span>
              <span>LIVE</span>
            </div>
          ) : (
            <div className="flex items-center space-x-2 bg-cyber-rose/10 border border-cyber-rose/20 px-3 py-1 rounded-full text-xs text-cyber-rose font-mono">
              <WifiOff className="w-3.5 h-3.5" />
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyber-rose opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-cyber-rose"></span>
              </span>
              <span>CONNECTING</span>
            </div>
          )}
        </div>
      </header>

      {/* RENDER VIEW DEPENDING ON ACTIVE TAB */}
      <div className="flex-1 flex flex-col space-y-6">

        {/* TAB 1: OVERVIEW */}
        {activeTab === 'overview' && (
          <>
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
              {/* Wallet Watchlist Sidebar info */}
              <section className="lg:col-span-1 flex flex-col space-y-4 p-4 glass-panel rounded-xl glass-panel-glow border-cyber-border">
                <div className="flex items-center space-x-2 pb-3 border-b border-cyber-border/40">
                  <Compass className="w-5 h-5 text-indigo-400" />
                  <h2 className="text-sm font-semibold tracking-wider text-white font-mono">WALLET WATCHLIST</h2>
                </div>
                <div className="bg-cyber-cardLight/40 rounded-lg p-3 border border-cyber-border/40">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-xs font-semibold text-gray-300 font-mono">Whale Wallet A</span>
                    <span className={`h-2 w-2 rounded-full ${walletMonitor.whaleA.active ? 'bg-cyber-emerald shadow-[0_0_8px_#10B981]' : 'bg-cyber-rose shadow-[0_0_8px_#F43F5E]'}`}></span>
                  </div>
                  <p className="text-[10px] text-cyber-textMuted font-mono truncate">WhaleA11111111111111111111111111111111111</p>
                  <div className="mt-2 flex justify-between items-center text-xs">
                    <span className="text-cyber-textMuted">Activity:</span>
                    <span className="font-mono text-gray-200">{walletMonitor.whaleA.txCount}</span>
                  </div>
                </div>
                <div className="bg-cyber-cardLight/40 rounded-lg p-3 border border-cyber-border/40">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-xs font-semibold text-gray-300 font-mono">Whale Wallet B</span>
                    <span className={`h-2 w-2 rounded-full ${walletMonitor.whaleB.active ? 'bg-cyber-emerald shadow-[0_0_8px_#10B981]' : 'bg-cyber-rose shadow-[0_0_8px_#F43F5E]'}`}></span>
                  </div>
                  <p className="text-[10px] text-cyber-textMuted font-mono truncate">WhaleB22222222222222222222222222222222222</p>
                  <div className="mt-2 flex justify-between items-center text-xs">
                    <span className="text-cyber-textMuted">Activity:</span>
                    <span className="font-mono text-gray-200">{walletMonitor.whaleB.txCount}</span>
                  </div>
                </div>
                <div className="pt-2">
                  <div className="flex justify-between items-center text-xs py-2 border-b border-cyber-border/20">
                    <span className="text-cyber-textMuted font-mono">Trigger Window:</span>
                    <span className="font-semibold text-gray-300 font-mono">{walletMonitor.triggerWindow}</span>
                  </div>
                  <div className="flex justify-between items-center text-xs py-2 border-b border-cyber-border/20">
                    <span className="text-cyber-textMuted font-mono">Last Trigger:</span>
                    <span className="font-semibold text-gray-300 font-mono">{walletMonitor.lastTrigger}</span>
                  </div>
                  <div className="flex justify-between items-center text-xs py-2 border-b border-cyber-border/20">
                    <span className="text-cyber-textMuted font-mono">Active Model:</span>
                    <span className="font-semibold text-indigo-400 font-mono">{walletMonitor.mlModel}</span>
                  </div>
                  <div className="flex justify-between items-center text-xs py-2">
                    <span className="text-cyber-textMuted font-mono">Accuracy (val):</span>
                    <span className="font-semibold text-cyber-emerald font-mono">{walletMonitor.accuracy}</span>
                  </div>
                </div>
              </section>

              {/* Core Telemetry Widgets */}
              <div className="lg:col-span-3 grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="md:col-span-2 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl">
                  <div className="flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40">
                    <Activity className="w-5 h-5 text-indigo-400" />
                    <h2 className="text-sm font-semibold tracking-wider text-white font-mono">LIVE SIGNAL FEED</h2>
                  </div>
                  <div className="flex-1 flex flex-col space-y-3 overflow-y-auto max-h-[220px] pr-1">
                    {liveSignals.length > 0 ? (
                      liveSignals.slice(0, 5).map((signal) => (
                        <div key={signal.id} className="flex items-center justify-between p-2.5 rounded-lg bg-cyber-cardLight border border-cyber-border/40 transition-all hover:border-indigo-500/30">
                          <div className="flex items-center space-x-3">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono ${
                              signal.direction === 'BUY' ? 'bg-cyber-emerald/10 text-cyber-emerald border border-cyber-emerald/20' :
                              signal.direction === 'SELL' ? 'bg-cyber-rose/10 text-cyber-rose border border-cyber-rose/20' :
                              'bg-cyber-amber/10 text-cyber-amber border border-cyber-amber/20'
                            }`}>
                              {signal.direction}
                            </span>
                            <div>
                              <span className="font-bold text-sm text-white font-mono">{signal.token}</span>
                              <p className="text-[10px] text-cyber-textMuted font-mono mt-0.5">{signal.details}</p>
                            </div>
                          </div>
                          <div className="text-right flex items-center space-x-2">
                            <button 
                              onClick={() => setSelectedSignal(signal)}
                              className="p-1 rounded bg-cyber-cardLight/80 border border-cyber-border hover:text-indigo-400 transition-colors"
                              title="View Detail metrics"
                            >
                              <Eye className="w-3.5 h-3.5" />
                            </button>
                            <div>
                              <div className="flex items-center space-x-1.5 justify-end">
                                <span className={`h-1.5 w-1.5 rounded-full ${signal.safetyPassed ? 'bg-cyber-emerald' : 'bg-cyber-rose'}`}></span>
                                <span className="text-xs font-bold text-indigo-400 font-mono">{signal.confidence}%</span>
                              </div>
                              <p className="text-[10px] text-cyber-textMuted font-mono mt-0.5">{signal.timestamp}</p>
                            </div>
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="text-xs text-cyber-textMuted/60 font-mono text-center py-10">No signals triggered yet.</div>
                    )}
                  </div>
                </div>

                <div className="md:col-span-1 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl items-center justify-between">
                  <div className="w-full flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40 justify-start">
                    <Gauge className="w-5 h-5 text-indigo-400" />
                    <h2 className="text-sm font-semibold tracking-wider text-white font-mono">CONFIDENCE</h2>
                  </div>
                  <div className="relative flex flex-col items-center justify-center py-2">
                    <svg className="w-36 h-36 transform -rotate-90">
                      <circle cx="72" cy="72" r="62" stroke="#161E30" strokeWidth="12" fill="transparent" />
                      <circle cx="72" cy="72" r="62" stroke="url(#gaugeGradient)" strokeWidth="12" fill="transparent" strokeDasharray="390"
                        strokeDashoffset={confidenceGaugeScore ? 390 - (390 * (confidenceGaugeScore * 0.75)) / 100 : 390}
                        strokeLinecap="round" className="transition-all duration-1000 ease-out"
                      />
                      <defs>
                        <linearGradient id="gaugeGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                          <stop offset="0%" stopColor="#4F46E5" />
                          <stop offset="100%" stopColor="#818CF8" />
                        </linearGradient>
                      </defs>
                    </svg>
                    <div className="absolute inset-0 flex flex-col items-center justify-center mt-2">
                      <span className="text-3xl font-extrabold text-white tracking-tight font-mono">{confidenceGaugeScore !== null ? `${confidenceGaugeScore}%` : '--'}</span>
                      <span className="text-[10px] text-cyber-textMuted font-mono uppercase tracking-widest mt-0.5">ML PREDICTION</span>
                    </div>
                  </div>
                  <div className="w-full text-center border-t border-cyber-border/20 pt-3 mt-1">
                    <span className="text-xs text-cyber-textMuted font-mono">Last signal threshold: </span>
                    <span className="text-xs font-bold text-gray-300 font-mono">{confidenceThreshold}%</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Diagnostics Component Status widget */}
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
              <section className="lg:col-span-2 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl">
                <div className="flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40">
                  <Server className="w-5 h-5 text-indigo-400" />
                  <h2 className="text-sm font-semibold tracking-wider text-white font-mono">SYSTEM COMPONENT STATUS</h2>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="p-2.5 rounded bg-cyber-cardLight/30 border border-cyber-border/30 flex flex-col justify-between">
                    <div>
                      <span className="text-[10px] text-cyber-textMuted font-mono uppercase">System Health</span>
                      <p className={`text-lg font-bold font-mono ${systemStatus.overall_status === 'healthy' ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>
                        {systemStatus.overall_status.toUpperCase()}
                      </p>
                    </div>
                    <div className="mt-2 text-[10px] text-cyber-textMuted font-mono">
                      RPC mode: <span className="text-indigo-400 font-bold font-mono">{systemStatus.rpc_status.toUpperCase()}</span>
                    </div>
                  </div>
                  {systemStatus.components && systemStatus.components.slice(0, 3).map((comp) => (
                    <div key={comp.name} className="p-2.5 rounded bg-cyber-cardLight/40 border border-cyber-border/20 flex justify-between items-center">
                      <div>
                        <span className="text-xs font-semibold font-mono text-gray-200">{comp.name}</span>
                        <p className="text-[9px] text-cyber-textMuted font-mono leading-tight">{comp.detail}</p>
                      </div>
                      <span className={`px-2 py-0.5 rounded text-[8px] font-bold font-mono ${
                        comp.status === 'running' ? 'bg-cyber-emerald/10 text-cyber-emerald border border-cyber-emerald/20' :
                        comp.status === 'error' ? 'bg-cyber-rose/10 text-cyber-rose border border-cyber-rose/20' :
                        'bg-cyber-textMuted/10 text-cyber-textMuted border border-cyber-textMuted/20'
                      }`}>
                        {comp.status.toUpperCase()}
                      </span>
                    </div>
                  ))}
                </div>
              </section>

              {/* Dynamic Discovery candidates inline widget */}
              <section className="lg:col-span-2 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl">
                <div className="flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40">
                  <Zap className="w-5 h-5 text-cyber-amber" />
                  <h2 className="text-sm font-semibold tracking-wider text-white font-mono">DYNAMIC CANDIDATES LIMIT</h2>
                </div>
                <div className="flex-1 flex flex-col justify-center space-y-2 pr-1">
                  {walletCandidates.length > 0 ? (
                    walletCandidates.slice(0, 2).map((cand) => (
                      <div key={cand.wallet_address} className="p-2.5 rounded bg-cyber-cardLight border border-cyber-border/40 flex justify-between items-center text-xs">
                        <div>
                          <span className="font-bold font-mono text-gray-200">{cand.wallet_short}</span>
                          <p className="text-[10px] text-cyber-textMuted font-mono">Reason: {cand.discovery_reason}</p>
                        </div>
                        <div className="flex space-x-1">
                          <button onClick={() => handleWalletAction(cand.wallet_address, 'approve')} className="p-1 bg-cyber-emerald/10 border border-cyber-emerald/20 text-cyber-emerald rounded hover:bg-cyber-emerald hover:text-white transition-colors cursor-pointer">
                            <Check className="w-3 h-3" />
                          </button>
                          <button onClick={() => handleWalletAction(cand.wallet_address, 'reject')} className="p-1 bg-cyber-rose/10 border border-cyber-rose/20 text-cyber-rose rounded hover:bg-cyber-rose hover:text-white transition-colors cursor-pointer">
                            <X className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="text-xs text-cyber-textMuted/60 font-mono text-center py-6">No candidates awaiting approval.</div>
                  )}
                </div>
              </section>
            </div>
          </>
        )}

        {/* TAB 2: WATCHLIST */}
        {activeTab === 'watchlist' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <section className="lg:col-span-2 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl">
              <div className="flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40">
                <ListFilter className="w-5 h-5 text-indigo-400" />
                <h2 className="text-sm font-semibold tracking-wider text-white font-mono">AUTO DISCOVERED CANDIDATES</h2>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left font-mono text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-cyber-border/60 text-cyber-textMuted uppercase font-bold text-[10px]">
                      <th className="py-2.5 px-3">Address</th>
                      <th className="py-2.5 px-3">Discovery Reason</th>
                      <th className="py-2.5 px-3">Discovered At</th>
                      <th className="py-2.5 px-3">Status</th>
                      <th className="py-2.5 px-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {walletCandidates.length > 0 ? (
                      walletCandidates.map((cand) => (
                        <tr key={cand.wallet_address} className="border-b border-cyber-border/20 hover:bg-cyber-cardLight/30 transition-all">
                          <td className="py-3 px-3 font-bold text-gray-200">{cand.wallet_address}</td>
                          <td className="py-3 px-3 text-cyber-textMuted">{cand.discovery_reason}</td>
                          <td className="py-3 px-3 text-cyber-textMuted/80">{new Date(cand.discovered_at).toLocaleString()}</td>
                          <td className="py-3 px-3">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              cand.status === 'approved' ? 'bg-cyber-emerald/10 text-cyber-emerald' :
                              cand.status === 'rejected' ? 'bg-cyber-rose/10 text-cyber-rose' :
                              'bg-cyber-amber/10 text-cyber-amber'
                            }`}>
                              {cand.status.toUpperCase()}
                            </span>
                          </td>
                          <td className="py-3 px-3 text-right">
                            {cand.status === 'pending' ? (
                              <div className="flex space-x-2 justify-end">
                                <button onClick={() => handleWalletAction(cand.wallet_address, 'approve')} className="p-1 bg-cyber-emerald/10 border border-cyber-emerald/20 text-cyber-emerald rounded hover:bg-cyber-emerald hover:text-white transition-colors cursor-pointer">
                                  <Check className="w-3.5 h-3.5" />
                                </button>
                                <button onClick={() => handleWalletAction(cand.wallet_address, 'reject')} className="p-1 bg-cyber-rose/10 border border-cyber-rose/20 text-cyber-rose rounded hover:bg-cyber-rose hover:text-white transition-colors cursor-pointer">
                                  <X className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            ) : (
                              <span className="text-[10px] text-cyber-textMuted opacity-60">Locked</span>
                            )}
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={5} className="text-center py-10 text-cyber-textMuted/60">No discovered candidates on record.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="lg:col-span-1 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl">
              <div className="flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40">
                <Compass className="w-5 h-5 text-indigo-400" />
                <h2 className="text-sm font-semibold tracking-wider text-white font-mono">ACTIVE TARGETS</h2>
              </div>
              <div className="space-y-4">
                <div className="p-3.5 rounded bg-cyber-cardLight border border-cyber-border/40">
                  <p className="text-xs font-bold text-gray-300 font-mono">Whale Wallet A</p>
                  <p className="text-[10px] text-cyber-textMuted font-mono mt-1 break-all">Wha1eA11111111111111111111111111111111111</p>
                  <span className="mt-2.5 inline-flex items-center px-2 py-0.5 rounded text-[8px] font-bold bg-cyber-emerald/10 text-cyber-emerald border border-cyber-emerald/20 font-mono uppercase">PRIMARY TARGET</span>
                </div>
                <div className="p-3.5 rounded bg-cyber-cardLight border border-cyber-border/40">
                  <p className="text-xs font-bold text-gray-300 font-mono">Whale Wallet B</p>
                  <p className="text-[10px] text-cyber-textMuted font-mono mt-1 break-all">Wha1eB22222222222222222222222222222222222</p>
                  <span className="mt-2.5 inline-flex items-center px-2 py-0.5 rounded text-[8px] font-bold bg-cyber-emerald/10 text-cyber-emerald border border-cyber-emerald/20 font-mono uppercase">PRIMARY TARGET</span>
                </div>
              </div>
            </section>
          </div>
        )}

        {/* TAB 3: SIGNALS */}
        {activeTab === 'signals' && (
          <section className="flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl">
            <div className="flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40">
              <Activity className="w-5 h-5 text-indigo-400" />
              <h2 className="text-sm font-semibold tracking-wider text-white font-mono">SIGNAL HISTORY FEED</h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left font-mono text-xs border-collapse">
                <thead>
                  <tr className="border-b border-cyber-border/60 text-cyber-textMuted uppercase font-bold text-[10px]">
                    <th className="py-2.5 px-3">Signal ID</th>
                    <th className="py-2.5 px-3">Token Mint</th>
                    <th className="py-2.5 px-3">Source Wallet</th>
                    <th className="py-2.5 px-3">Direction</th>
                    <th className="py-2.5 px-3">Confidence</th>
                    <th className="py-2.5 px-3">Safety Status</th>
                    <th className="py-2.5 px-3">Timestamp</th>
                    <th className="py-2.5 px-3 text-right">Drill-Down</th>
                  </tr>
                </thead>
                <tbody>
                  {liveSignals.length > 0 ? (
                    liveSignals.map((sig) => (
                      <tr key={sig.id} className="border-b border-cyber-border/20 hover:bg-cyber-cardLight/30 transition-all">
                        <td className="py-3 px-3 font-semibold text-gray-200">{sig.id}</td>
                        <td className="py-3 px-3 text-indigo-400 font-bold">{sig.token}</td>
                        <td className="py-3 px-3 text-cyber-textMuted">{sig.details}</td>
                        <td className="py-3 px-3">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            sig.direction === 'BUY' ? 'bg-cyber-emerald/10 text-cyber-emerald' : 'bg-cyber-rose/10 text-cyber-rose'
                          }`}>
                            {sig.direction}
                          </span>
                        </td>
                        <td className="py-3 px-3 font-bold text-gray-100">{sig.confidence}%</td>
                        <td className="py-3 px-3">
                          <span className={`h-2 w-2 rounded-full inline-block mr-1.5 ${sig.safetyPassed ? 'bg-cyber-emerald shadow-[0_0_8px_#10B981]' : 'bg-cyber-rose shadow-[0_0_8px_#F43F5E]'}`}></span>
                          <span className="text-[10px] text-cyber-textMuted">{sig.safetyPassed ? 'PASSED' : 'BLOCKED'}</span>
                        </td>
                        <td className="py-3 px-3 text-cyber-textMuted/80">{sig.timestamp}</td>
                        <td className="py-3 px-3 text-right">
                          <button onClick={() => setSelectedSignal(sig)} className="px-2.5 py-1 bg-cyber-cardLight hover:bg-indigo-600 hover:text-white rounded border border-cyber-border transition-colors cursor-pointer">
                            Inspect ML
                          </button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={8} className="text-center py-12 text-cyber-textMuted/60">No signals triggered in specified window.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* TAB 4: TRADES */}
        {activeTab === 'trades' && (
          <section className="flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl">
            <div className="flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40">
              <ListTodo className="w-5 h-5 text-indigo-400" />
              <h2 className="text-sm font-semibold tracking-wider text-white font-mono">CLOSED TRADES LOG</h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left font-mono text-xs border-collapse">
                <thead>
                  <tr className="border-b border-cyber-border/60 text-cyber-textMuted uppercase font-bold text-[10px]">
                    <th className="py-2.5 px-3">Trade ID</th>
                    <th className="py-2.5 px-3">Token</th>
                    <th className="py-2.5 px-3">Direction</th>
                    <th className="py-2.5 px-3">PnL Pct</th>
                    <th className="py-2.5 px-3">R-Multiple</th>
                    <th className="py-2.5 px-3">Holding Time</th>
                    <th className="py-2.5 px-3">Exit Reason</th>
                    <th className="py-2.5 px-3 text-right">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {tradeLog.length > 0 ? (
                    tradeLog.map((trade) => (
                      <tr key={trade.id} className="border-b border-cyber-border/20 hover:bg-cyber-cardLight/30 transition-all">
                        <td className="py-3 px-3 text-gray-300">{trade.id}</td>
                        <td className="py-3 px-3 font-bold text-white">{trade.token}</td>
                        <td className="py-3 px-3 text-cyber-textMuted">{trade.direction}</td>
                        <td className={`py-3 px-3 font-bold ${trade.isPositive ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>{trade.pnl}</td>
                        <td className={`py-3 px-3 font-bold ${trade.isPositive ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>
                          {trade.isPositive ? '+' : ''}{(parseFloat(trade.pnl) / 10).toFixed(2)}R
                        </td>
                        <td className="py-3 px-3 text-cyber-textMuted">12 min</td>
                        <td className="py-3 px-3">
                          <span className="text-[10px] bg-cyber-cardLight px-2 py-0.5 rounded border border-cyber-border text-gray-200">
                            trailing_tp
                          </span>
                        </td>
                        <td className="py-3 px-3 text-right">
                          <button onClick={() => setSelectedTrade(trade)} className="px-2.5 py-1 bg-cyber-cardLight hover:bg-indigo-600 hover:text-white rounded border border-cyber-border transition-colors cursor-pointer">
                            Inspect
                          </button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={8} className="text-center py-12 text-cyber-textMuted/60">No closed trades recorded in SQLite repository.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* TAB 5: DIAGNOSTICS */}
        {activeTab === 'diagnostics' && (
          <section className="flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl">
            <div className="flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40">
              <AlertOctagon className="w-5 h-5 text-cyber-rose" />
              <h2 className="text-sm font-semibold tracking-wider text-white font-mono">F-19 DIAGNOSTICS ERROR REGISTRY</h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left font-mono text-xs border-collapse">
                <thead>
                  <tr className="border-b border-cyber-border/60 text-cyber-textMuted uppercase font-bold text-[10px]">
                    <th className="py-2.5 px-3">Log ID</th>
                    <th className="py-2.5 px-3">Timestamp</th>
                    <th className="py-2.5 px-3">Error Type</th>
                    <th className="py-2.5 px-3">Severity</th>
                    <th className="py-2.5 px-3">Context Description</th>
                    <th className="py-2.5 px-3">Recovery Action</th>
                    <th className="py-2.5 px-3 text-right">Drill-Down</th>
                  </tr>
                </thead>
                <tbody>
                  {errorLogs.length > 0 ? (
                    errorLogs.map((log) => (
                      <tr key={log.log_id} className="border-b border-cyber-border/20 hover:bg-cyber-cardLight/30 transition-all">
                        <td className="py-3 px-3 text-gray-300 font-bold">{log.log_id}</td>
                        <td className="py-3 px-3 text-cyber-textMuted/80">{new Date(log.timestamp).toLocaleString()}</td>
                        <td className="py-3 px-3 text-indigo-400 font-bold">{log.error_type}</td>
                        <td className="py-3 px-3">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            log.severity === 'CRITICAL' ? 'bg-cyber-rose text-white animate-pulse' :
                            log.severity === 'ERROR' ? 'bg-cyber-rose/10 text-cyber-rose' :
                            log.severity === 'WARNING' ? 'bg-cyber-amber/10 text-cyber-amber' :
                            'bg-cyber-textMuted/10 text-cyber-textMuted'
                          }`}>
                            {log.severity}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-cyber-textMuted truncate max-w-xs">{log.context}</td>
                        <td className="py-3 px-3 text-gray-200">{log.recovery_action}</td>
                        <td className="py-3 px-3 text-right">
                          <button onClick={() => setSelectedError(log)} className="px-2.5 py-1 bg-cyber-cardLight hover:bg-indigo-600 hover:text-white rounded border border-cyber-border transition-colors cursor-pointer">
                            Inspect Log
                          </button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={7} className="text-center py-12 text-cyber-textMuted/60">No diagnostics errors recorded in SQLite database. System operational metrics stable.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

      </div>

      {/* FOOTER STATS */}
      {activeTab === 'overview' && (
        <footer className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-6">
          <div className="flex justify-between items-center p-4 bg-cyber-card border border-cyber-emerald/20 shadow-[0_0_15px_-3px_rgba(16,185,129,0.05)] rounded-xl">
            <div className="space-y-1">
              <span className="text-xs text-cyber-emerald font-mono uppercase tracking-wider">Win Rate</span>
              <div className="text-3xl font-extrabold text-white font-mono">{metrics.winRate}</div>
            </div>
            <div className="p-3 bg-cyber-emerald/10 text-cyber-emerald rounded-lg border border-cyber-emerald/20">
              <TrendingUp className="w-6 h-6" />
            </div>
          </div>
          <div className="flex justify-between items-center p-4 bg-cyber-card border border-indigo-500/20 shadow-[0_0_15px_-3px_rgba(79,70,229,0.05)] rounded-xl">
            <div className="space-y-1">
              <span className="text-xs text-indigo-400 font-mono uppercase tracking-wider">Triggers Today</span>
              <div className="text-3xl font-extrabold text-white font-mono">{metrics.triggersToday}</div>
            </div>
            <div className="p-3 bg-indigo-500/10 text-indigo-400 rounded-lg border border-indigo-500/20">
              <Clock className="w-6 h-6" />
            </div>
          </div>
          <div className="flex justify-between items-center p-4 bg-cyber-card border border-cyber-amber/20 shadow-[0_0_15px_-3px_rgba(245,158,11,0.05)] rounded-xl">
            <div className="space-y-1">
              <span className="text-xs text-cyber-amber font-mono uppercase tracking-wider">Alerts Fired</span>
              <div className="text-3xl font-extrabold text-white font-mono">
                {metrics.alertsFiredCount !== '--' && metrics.alertsFiredTotal !== '--'
                  ? `${metrics.alertsFiredCount} / ${metrics.alertsFiredTotal}`
                  : '--'}
              </div>
            </div>
            <div className="p-3 bg-cyber-amber/10 text-cyber-amber rounded-lg border border-cyber-amber/20">
              <RotateCw className="w-6 h-6" />
            </div>
          </div>
        </footer>
      )}

      {/* DRILL-DOWN DETAILED MODALS */}
      {selectedSignal && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50 animate-fadeIn">
          <div className="bg-cyber-card border border-cyber-border rounded-xl max-w-lg w-full p-6 space-y-4 shadow-2xl">
            <div className="flex justify-between items-center border-b border-cyber-border/40 pb-3">
              <h3 className="text-base font-bold font-mono text-white">Signal Drill-Down: {selectedSignal.id}</h3>
              <button onClick={() => setSelectedSignal(null)} className="text-cyber-textMuted hover:text-white p-1 rounded">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-3 font-mono text-xs">
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">Token Symbol:</span>
                <span className="font-bold text-indigo-400">{selectedSignal.token}</span>
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">Confidence:</span>
                <span className="font-bold text-white">{selectedSignal.confidence}%</span>
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">Safety Status:</span>
                <span className="font-bold text-cyber-emerald">{selectedSignal.safetyPassed ? 'PASSED' : 'BLOCKED'}</span>
              </div>
              <div>
                <p className="text-xs font-bold text-gray-300 mb-1.5">ML Feature Input Vectors:</p>
                <div className="bg-cyber-cardLight p-3 rounded border border-cyber-border/40 space-y-1 text-[10px] text-cyber-textMuted">
                  <div>• position_size_usd: <span className="text-gray-300">150.00</span></div>
                  <div>• token_age_minutes: <span className="text-gray-300">120.00</span></div>
                  <div>• liquidity_pool_depth: <span className="text-gray-300">15,000.00</span></div>
                  <div>• slippage_actual: <span className="text-gray-300">0.012</span></div>
                  <div>• win_rate_30d: <span className="text-gray-300">0.55</span></div>
                  <div>• avg_holding_time_minutes: <span className="text-gray-300">15.0</span></div>
                  <div>• sol_usd_momentum: <span className="text-gray-300">0.024</span></div>
                </div>
              </div>
            </div>
            <div className="text-right pt-3">
              <button onClick={() => setSelectedSignal(null)} className="px-4 py-1.5 bg-indigo-600 text-white rounded text-xs font-mono font-bold cursor-pointer">
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {selectedTrade && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50 animate-fadeIn">
          <div className="bg-cyber-card border border-cyber-border rounded-xl max-w-lg w-full p-6 space-y-4 shadow-2xl">
            <div className="flex justify-between items-center border-b border-cyber-border/40 pb-3">
              <h3 className="text-base font-bold font-mono text-white">Trade Inspect: {selectedTrade.id}</h3>
              <button onClick={() => setSelectedTrade(null)} className="text-cyber-textMuted hover:text-white p-1 rounded">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-3 font-mono text-xs">
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">Token Address:</span>
                <span className="font-bold text-gray-200">{selectedTrade.token}</span>
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">PnL Actual:</span>
                <span className={`font-bold ${selectedTrade.isPositive ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>{selectedTrade.pnl}</span>
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">R-Multiple Earned:</span>
                <span className="font-bold text-white">{(parseFloat(selectedTrade.pnl)/10).toFixed(2)}R</span>
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">Exit Reason:</span>
                <span className="font-bold text-amber-400">trailing_tp</span>
              </div>
            </div>
            <div className="text-right pt-3">
              <button onClick={() => setSelectedTrade(null)} className="px-4 py-1.5 bg-indigo-600 text-white rounded text-xs font-mono font-bold cursor-pointer">
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {selectedError && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50 animate-fadeIn">
          <div className="bg-cyber-card border border-cyber-border rounded-xl max-w-lg w-full p-6 space-y-4 shadow-2xl">
            <div className="flex justify-between items-center border-b border-cyber-border/40 pb-3">
              <h3 className="text-base font-bold font-mono text-white">Diagnostic Error details: {selectedError.log_id}</h3>
              <button onClick={() => setSelectedError(null)} className="text-cyber-textMuted hover:text-white p-1 rounded">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-3 font-mono text-xs">
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">Error Type:</span>
                <span className="font-bold text-rose-500 font-mono">{selectedError.error_type}</span>
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">Severity Level:</span>
                <span className="font-bold text-white">{selectedError.severity}</span>
              </div>
              <div className="pb-2 border-b border-cyber-border/20">
                <span className="text-cyber-textMuted block mb-1">Context:</span>
                <p className="p-2 bg-cyber-cardLight rounded text-[10px] text-gray-300 break-words font-mono leading-relaxed">{selectedError.context}</p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <span className="text-cyber-textMuted">Recovery Action:</span>
                <span className="font-bold text-cyber-emerald">{selectedError.recovery_action}</span>
              </div>
            </div>
            <div className="text-right pt-3">
              <button onClick={() => setSelectedError(null)} className="px-4 py-1.5 bg-indigo-600 text-white rounded text-xs font-mono font-bold cursor-pointer">
                Close
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  )
}

export default Dashboard
