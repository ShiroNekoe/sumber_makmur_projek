import React from 'react'
import { 
  Activity, 
  Wifi, 
  WifiOff, 
  TrendingUp, 
  Gauge, 
  Clock, 
  BarChart2, 
  ListTodo, 
  ShieldCheck, 
  RotateCw, 
  Compass, 
  Cpu
} from 'lucide-react'
import { useStore } from '../store/useStore'

export const Dashboard: React.FC = () => {
  const { isConnected, walletMonitor, liveSignals, confidenceGaugeScore, confidenceThreshold, confidenceHistory, tradeLog, metrics } = useStore()

  return (
    <div className="flex-1 flex flex-col p-4 md:p-6 bg-cyber-bg min-h-screen">
      {/* Header Panel */}
      <header className="flex justify-between items-center pb-4 mb-6 border-b border-cyber-border/60">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-indigo-600/10 rounded-lg border border-indigo-500/20 text-indigo-400">
            <Cpu className="w-6 h-6 animate-pulse" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-wider text-white">SUMBER MAKMUR SYSTEM</h1>
            <p className="text-xs text-cyber-textMuted font-mono">5-LAYER TRANSACTION ANALYSIS ENGINE</p>
          </div>
        </div>

        {/* WebSocket status light */}
        <div className="flex items-center space-x-2">
          {isConnected ? (
            <div className="flex items-center space-x-2 bg-cyber-emerald/10 border border-cyber-emerald/20 px-3 py-1 rounded-full text-xs text-cyber-emerald font-mono">
              <Wifi className="w-3.5 h-3.5" />
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyber-emerald opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-cyber-emerald"></span>
              </span>
              <span>LIVE: WEBSOCKET</span>
            </div>
          ) : (
            <div className="flex items-center space-x-2 bg-cyber-rose/10 border border-cyber-rose/20 px-3 py-1 rounded-full text-xs text-cyber-rose font-mono">
              <WifiOff className="w-3.5 h-3.5" />
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyber-rose opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-cyber-rose"></span>
              </span>
              <span>OFFLINE: CONNECTING</span>
            </div>
          )}
        </div>
      </header>

      {/* Main Content Grid */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-4 gap-6 mb-6">
        
        {/* LEFT COLUMN: Wallet Monitor Sidebar */}
        <section className="lg:col-span-1 flex flex-col space-y-4 p-4 glass-panel rounded-xl glass-panel-glow border-cyber-border">
          <div className="flex items-center space-x-2 pb-3 border-b border-cyber-border/40">
            <Compass className="w-5 h-5 text-indigo-400" />
            <h2 className="text-sm font-semibold tracking-wider text-white font-mono">WALLET MONITOR</h2>
          </div>

          {/* Whale Wallet A status */}
          <div className="bg-cyber-cardLight/40 rounded-lg p-3 border border-cyber-border/40">
            <div className="flex justify-between items-center mb-1">
              <span className="text-xs font-semibold text-gray-300 font-mono">Whale Wallet A</span>
              <span className={`h-2 w-2 rounded-full ${walletMonitor.whaleA.active ? 'bg-cyber-emerald shadow-[0_0_8px_#10B981]' : 'bg-gray-700'}`}></span>
            </div>
            <p className="text-[10px] text-cyber-textMuted font-mono truncate">WhaleA11111111111111111111111111111111111</p>
            <div className="mt-2 flex justify-between items-center text-xs">
              <span className="text-cyber-textMuted">Activity:</span>
              <span className="font-mono text-gray-200">{walletMonitor.whaleA.active ? walletMonitor.whaleA.txCount : '-- tx/hr'}</span>
            </div>
          </div>

          {/* Whale Wallet B status */}
          <div className="bg-cyber-cardLight/40 rounded-lg p-3 border border-cyber-border/40">
            <div className="flex justify-between items-center mb-1">
              <span className="text-xs font-semibold text-gray-300 font-mono">Whale Wallet B</span>
              <span className={`h-2 w-2 rounded-full ${walletMonitor.whaleB.active ? 'bg-cyber-emerald shadow-[0_0_8px_#10B981]' : 'bg-gray-700'}`}></span>
            </div>
            <p className="text-[10px] text-cyber-textMuted font-mono truncate">WhaleB22222222222222222222222222222222222</p>
            <div className="mt-2 flex justify-between items-center text-xs">
              <span className="text-cyber-textMuted">Activity:</span>
              <span className="font-mono text-gray-200">{walletMonitor.whaleB.active ? walletMonitor.whaleB.txCount : '-- tx/hr'}</span>
            </div>
          </div>

          {/* Trigger Window configurations */}
          <div className="pt-2">
            <div className="flex justify-between items-center text-xs py-2 border-b border-cyber-border/20">
              <span className="text-cyber-textMuted font-mono">Trigger Window:</span>
              <span className="font-semibold text-gray-300 font-mono">{walletMonitor.triggerWindow || '5 min - AND/OR'}</span>
            </div>
            <div className="flex justify-between items-center text-xs py-2 border-b border-cyber-border/20">
              <span className="text-cyber-textMuted font-mono">Last Trigger:</span>
              <span className="font-semibold text-gray-300 font-mono">{walletMonitor.lastTrigger || '--:--:-- UTC'}</span>
            </div>
            <div className="flex justify-between items-center text-xs py-2 border-b border-cyber-border/20">
              <span className="text-cyber-textMuted font-mono">ML Model:</span>
              <span className="font-semibold text-gray-300 font-mono">{walletMonitor.mlModel || 'v0 (Bootstrap)'}</span>
            </div>
            <div className="flex justify-between items-center text-xs py-2 border-b border-cyber-border/20">
              <span className="text-cyber-textMuted font-mono">Next Retrain:</span>
              <div className="flex items-center space-x-1 font-semibold text-gray-300 font-mono">
                <RotateCw className="w-3.5 h-3.5 text-indigo-400/80 animate-spin" style={{ animationDuration: '6s' }} />
                <span>{walletMonitor.nextRetrain || '02:00 UTC'}</span>
              </div>
            </div>
            <div className="flex justify-between items-center text-xs py-2">
              <span className="text-cyber-textMuted font-mono">Accuracy (val):</span>
              <span className="font-semibold text-cyber-emerald font-mono">{walletMonitor.accuracy || '--%'}</span>
            </div>
          </div>
        </section>

        {/* RIGHT AREA: 2x2 Core Telemetry Grid */}
        <div className="lg:col-span-3 grid grid-cols-1 md:grid-cols-3 gap-6">

          {/* Top Row Column 1 & 2: Live Signal Feed */}
          <div className="md:col-span-2 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl">
            <div className="flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40">
              <Activity className="w-5 h-5 text-indigo-400" />
              <h2 className="text-sm font-semibold tracking-wider text-white font-mono">LIVE SIGNAL FEED</h2>
            </div>

            <div className="flex-1 flex flex-col space-y-3 overflow-y-auto max-h-[220px] pr-1">
              {liveSignals.length > 0 ? (
                liveSignals.map((signal) => (
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
                    <div className="text-right">
                      <span className="text-xs font-bold text-indigo-400 font-mono">{signal.confidence}% conf</span>
                      <p className="text-[10px] text-cyber-textMuted font-mono mt-0.5">{signal.timestamp}</p>
                    </div>
                  </div>
                ))
              ) : (
                /* Skeleton Loading for Signals */
                <div className="space-y-3">
                  {[1, 2, 3].map((n) => (
                    <div key={n} className="flex items-center justify-between p-3 rounded-lg bg-cyber-cardLight/30 border border-cyber-border/20">
                      <div className="flex items-center space-x-3 w-3/4">
                        {/* Direction badge skeleton */}
                        <div className="w-12 h-5 rounded shimmer"></div>
                        {/* Token and details skeleton */}
                        <div className="flex-1 space-y-2">
                          <div className="w-1/3 h-4 rounded shimmer"></div>
                          <div className="w-2/3 h-3 rounded shimmer"></div>
                        </div>
                      </div>
                      <div className="w-1/6 flex flex-col items-end space-y-1.5">
                        <div className="w-full h-4 rounded shimmer"></div>
                        <div className="w-2/3 h-3 rounded shimmer"></div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Top Row Column 3: Confidence Score Gauge */}
          <div className="md:col-span-1 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl items-center justify-between">
            <div className="w-full flex items-center space-x-2 pb-3 mb-3 border-b border-cyber-border/40 justify-start">
              <Gauge className="w-5 h-5 text-indigo-400" />
              <h2 className="text-sm font-semibold tracking-wider text-white font-mono">CONFIDENCE</h2>
            </div>

            {/* Gauge Graphic */}
            <div className="relative flex flex-col items-center justify-center py-2">
              {/* Radial Gauge SVG */}
              <svg className="w-36 h-36 transform -rotate-90">
                <circle
                  cx="72"
                  cy="72"
                  r="62"
                  stroke="#161E30"
                  strokeWidth="12"
                  fill="transparent"
                />
                <circle
                  cx="72"
                  cy="72"
                  r="62"
                  stroke="url(#gaugeGradient)"
                  strokeWidth="12"
                  fill="transparent"
                  strokeDasharray="390"
                  strokeDashoffset={
                    confidenceGaugeScore 
                      ? 390 - (390 * (confidenceGaugeScore * 0.75)) / 100 // Scale gauge Arc (0.75 matches semicircle)
                      : 390
                  }
                  strokeLinecap="round"
                  className="transition-all duration-1000 ease-out"
                />
                <defs>
                  <linearGradient id="gaugeGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#4F46E5" />
                    <stop offset="100%" stopColor="#818CF8" />
                  </linearGradient>
                </defs>
              </svg>

              {/* Central Value */}
              <div className="absolute inset-0 flex flex-col items-center justify-center mt-2">
                <span className="text-3xl font-extrabold text-white tracking-tight font-mono">
                  {confidenceGaugeScore !== null ? `${confidenceGaugeScore}%` : '--'}
                </span>
                <span className="text-[10px] text-cyber-textMuted font-mono uppercase tracking-widest mt-0.5">ML PREDICTION</span>
              </div>
            </div>

            {/* Metrics */}
            <div className="w-full text-center border-t border-cyber-border/20 pt-3 mt-1">
              <span className="text-xs text-cyber-textMuted font-mono">Last signal threshold: </span>
              <span className="text-xs font-bold text-gray-300 font-mono">{confidenceThreshold}%</span>
            </div>
          </div>

          {/* Bottom Row Column 1 & 2: Confidence History (24h Chart area) */}
          <div className="md:col-span-2 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl justify-between">
            <div className="flex items-center space-x-2 pb-3 border-b border-cyber-border/40">
              <BarChart2 className="w-5 h-5 text-indigo-400" />
              <h2 className="text-sm font-semibold tracking-wider text-white font-mono">CONFIDENCE HISTORY (24H)</h2>
            </div>

            <div className="relative flex-1 min-h-[160px] flex items-center justify-center mt-3">
              {confidenceHistory.length > 0 ? (
                /* Plotting SVG logic if data is loaded */
                <div className="w-full h-full"></div>
              ) : (
                /* Empty / Skeleton Chart UI */
                <div className="w-full h-full flex flex-col justify-end relative">
                  {/* Grid Lines */}
                  <div className="absolute inset-0 flex flex-col justify-between py-2 pointer-events-none">
                    <div className="w-full border-t border-cyber-border/10"></div>
                    {/* Dotted threshold line at 75% height */}
                    <div className="w-full border-t border-dashed border-cyber-amber/30 relative">
                      <span className="absolute right-0 -top-2 px-1 text-[8px] text-cyber-amber bg-cyber-card font-mono">THRESHOLD 75%</span>
                    </div>
                    <div className="w-full border-t border-cyber-border/10"></div>
                    <div className="w-full border-b border-cyber-border/10"></div>
                  </div>

                  {/* Skeletons Bars */}
                  <div className="flex justify-around items-end h-[100px] px-4 z-10">
                    {[0.3, 0.5, 0.4, 0.2, 0.7, 0.6, 0.1, 0.5, 0.3, 0.6, 0.4, 0.5].map((opacity, idx) => (
                      <div 
                        key={idx} 
                        className="w-5 md:w-7 bg-indigo-500/10 rounded-t border-t border-x border-indigo-500/20"
                        style={{ height: `${opacity * 100}%`, opacity: 0.3 + (idx % 3) * 0.15 }}
                      ></div>
                    ))}
                  </div>

                  {/* Empty message overlay */}
                  <div className="absolute inset-0 flex flex-col items-center justify-center z-20">
                    <p className="text-xs text-cyber-textMuted font-mono tracking-wider">WAITING FOR NETWORK SIGNAL EVENT...</p>
                    <span className="text-[10px] text-cyber-textMuted/60 mt-1 font-mono">CHART LOADS UPON MODEL EVALUATION</span>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Bottom Row Column 3: Trade Log */}
          <div className="md:col-span-1 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl justify-between">
            <div className="flex items-center space-x-2 pb-3 mb-2 border-b border-cyber-border/40">
              <ListTodo className="w-5 h-5 text-indigo-400" />
              <h2 className="text-sm font-semibold tracking-wider text-white font-mono">TRADE LOG</h2>
            </div>

            <div className="flex-1 flex flex-col space-y-2 overflow-y-auto max-h-[160px] pr-1">
              {tradeLog.length > 0 ? (
                tradeLog.map((trade) => (
                  <div key={trade.id} className="flex justify-between items-center py-2 px-2.5 rounded bg-cyber-cardLight/30 border border-cyber-border/20 text-xs">
                    <div className="flex items-center space-x-2">
                      <span className={`w-1 h-3 rounded ${trade.direction === 'BUY' ? 'bg-cyber-emerald' : 'bg-cyber-rose'}`}></span>
                      <span className="text-[10px] font-mono text-cyber-textMuted uppercase">{trade.direction}</span>
                      <span className="font-bold text-gray-200 font-mono">{trade.token}</span>
                    </div>
                    <span className={`font-mono font-bold ${trade.isPositive ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>
                      {trade.pnl}
                    </span>
                  </div>
                ))
              ) : (
                /* Skeleton rows for trade logs */
                <div className="space-y-2">
                  {[1, 2, 3, 4].map((n) => (
                    <div key={n} className="flex justify-between items-center py-2 px-2.5 rounded bg-cyber-cardLight/10 border border-cyber-border/10">
                      <div className="flex items-center space-x-2 w-2/3">
                        <div className="w-1 h-3 rounded bg-cyber-border"></div>
                        <div className="w-12 h-3 rounded shimmer"></div>
                        <div className="w-12 h-3.5 rounded shimmer"></div>
                      </div>
                      <div className="w-1/4 h-3.5 rounded shimmer"></div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

        </div>
      </div>

      {/* BOTTOM ROW: 3 Metric Cards */}
      <footer className="grid grid-cols-1 md:grid-cols-3 gap-6">
        
        {/* Metric 1: Win Rate */}
        <div className="flex justify-between items-center p-4 bg-cyber-card border border-cyber-emerald/20 shadow-[0_0_15px_-3px_rgba(16,185,129,0.05)] rounded-xl">
          <div className="space-y-1">
            <span className="text-xs text-cyber-emerald font-mono uppercase tracking-wider">Win Rate</span>
            <div className="text-3xl font-extrabold text-white font-mono">
              {metrics.winRate || '--'}
            </div>
          </div>
          <div className="p-3 bg-cyber-emerald/10 text-cyber-emerald rounded-lg border border-cyber-emerald/20">
            <TrendingUp className="w-6 h-6" />
          </div>
        </div>

        {/* Metric 2: Triggers Today */}
        <div className="flex justify-between items-center p-4 bg-cyber-card border border-indigo-500/20 shadow-[0_0_15px_-3px_rgba(79,70,229,0.05)] rounded-xl">
          <div className="space-y-1">
            <span className="text-xs text-indigo-400 font-mono uppercase tracking-wider">Triggers Today</span>
            <div className="text-3xl font-extrabold text-white font-mono">
              {metrics.triggersToday || '--'}
            </div>
          </div>
          <div className="p-3 bg-indigo-500/10 text-indigo-400 rounded-lg border border-indigo-500/20">
            <Clock className="w-6 h-6" />
          </div>
        </div>

        {/* Metric 3: Alerts Fired */}
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
            <RotateCw className="w-6 h-6 animate-pulse" />
          </div>
        </div>

      </footer>
    </div>
  )
}

export default Dashboard
