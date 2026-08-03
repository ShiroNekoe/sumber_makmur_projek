import React, { useEffect, useState } from 'react'
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
  Eye,
  Briefcase,
  Download,
  FileText,
  Plus,
  Trash2,
  Sparkles,
} from 'lucide-react'
import { useStore } from '../store/useStore'
import { 
  approveWallet, 
  triggerManualRetrain, 
  fetchWalletCandidates, 
  fetchSystemStatus, 
  fetchRecentTrades, 
  fetchRecentSignals, 
  fetchSystemErrors,
  fetchDashboardPortfolio,
  fetchActiveWallets,
  exportPortfolioPdfUrl,
  addManualWallet,
  deleteManualWallet,
  fetchMarketInsights,
  approveMarketInsight,
  rejectMarketInsight,
  triggerMarketInsightJob,
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
    portfolio,
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
    setTradeLog,
    setPortfolio,
    activeWallets,
    setActiveWallets,
  } = useStore()

  const [timeframe, setTimeframe] = useState<'1D' | '7D' | '30D' | '180D' | '360D'>('7D');
  const [hoveredPoint, setHoveredPoint] = useState<any | null>(null);
  const [hoveredPointIndex, setHoveredPointIndex] = useState<number | null>(null);
  
  // Watchlist Manual Addition & Deletion States
  const [newWalletAddress, setNewWalletAddress] = useState('');
  const [newWalletLabel, setNewWalletLabel] = useState('');

  // AI Market Insights States
  const [insights, setInsights] = useState<any[]>([]);
  const [insightFilter, setInsightFilter] = useState<string>('ALL');
  const [isGeneratingInsights, setIsGeneratingInsights] = useState<boolean>(false);
  const [isAddingWallet, setIsAddingWallet] = useState(false);
  const [isDeletingWallet, setIsDeletingWallet] = useState<string | null>(null);
  
  // PDF Report Export States
  const [pdfStartDate, setPdfStartDate] = useState<string>('');
  const [pdfEndDate, setPdfEndDate] = useState<string>('');
  const [isExportingPdf, setIsExportingPdf] = useState<boolean>(false);

  const handleSvgInteraction = (e: React.MouseEvent<SVGSVGElement> | React.TouchEvent<SVGSVGElement>, historyToUse: any[]) => {
    if (!historyToUse || historyToUse.length === 0) return;
    
    let clientX = 0;
    if ('touches' in e) {
      if (e.touches.length === 0) return;
      clientX = e.touches[0].clientX;
    } else {
      clientX = e.clientX;
    }
    
    const svgElement = e.currentTarget;
    const rect = svgElement.getBoundingClientRect();
    const relativeX = clientX - rect.left;
    
    const viewBoxX = (relativeX / rect.width) * 600;
    const chartWidth = 500;
    const chartStart = 50;
    const fraction = (viewBoxX - chartStart) / chartWidth;
    const index = Math.min(
      historyToUse.length - 1,
      Math.max(0, Math.round(fraction * (historyToUse.length - 1)))
    );
    
    const point = historyToUse[index];
    setHoveredPoint(point);
    setHoveredPointIndex(index);
  };

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
        const activeWalletsRes = await fetchActiveWallets()
        if (activeWalletsRes) {
          setActiveWallets(activeWalletsRes)
        }
        const portRes = await fetchDashboardPortfolio()
        if (portRes) {
          setPortfolio(portRes)
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
            // Replace entire trade log to avoid duplicates on tab re-open
            const mapped = tradesRes.trades.map((t: any) => ({
              id: t.trade_id,
              direction: t.direction,
              token: t.token_symbol || t.token_address.substring(0, 8),
              pnl: `${t.pnl_pct_actual >= 0 ? '+' : ''}${(t.pnl_pct_actual * 100).toFixed(1)}%`,
              isPositive: t.pnl_pct_actual >= 0,
              holdingTime: `${t.holding_time_minutes ?? '--'} min`,
              exitReason: t.exit_reason || 'unknown',
            }))
            setTradeLog(mapped)
          }
        } else if (activeTab === 'signals') {
          const sigsRes = await fetchRecentSignals(48)
          if (sigsRes && sigsRes.signals) {
            sigsRes.signals.forEach((s: any) => {
              addSignal({
                id: s.signal_id,
                direction: s.direction,
                token: s.token_short || s.token_symbol || (s.token_address ? `${s.token_address.slice(0,6)}...${s.token_address.slice(-4)}` : 'UNKNOWN'),
                confidence: Math.round(s.confidence_score * 100),
                timestamp: new Date(s.timestamp).toLocaleTimeString(),
                details: `Wallet: ${s.wallet_short || s.wallet_source?.slice(0,8) || ''}`,
                safetyPassed: s.safety_passed,
                features: s.features || null,
                // Extra fields for Drill-Down
                token_address: s.token_address,
                token_symbol: s.token_symbol || '',
                token_name: s.token_name || '',
                dex_url: s.dex_url || ''
              })
            })
          }
        } else if (activeTab === 'portfolio') {
          const portRes = await fetchDashboardPortfolio()
          if (portRes) {
            setPortfolio(portRes)
          }
        } else if (activeTab === 'insights') {
          const res = await fetchMarketInsights(insightFilter === 'ALL' ? undefined : insightFilter)
          if (res) {
            setInsights(res)
          }
        }
      } catch (err) {
        console.warn(`Failed loading data for tab ${activeTab}:`, err)
      }
    }
    fetchTabData()
  }, [activeTab, insightFilter])

  const handleApproveInsight = async (id: string) => {
    try {
      addNotification(`Approving insight ${id}...`, 'info')
      await approveMarketInsight(id)
      addNotification('Insight approved and added to retrain candidates!', 'success')
      const updated = await fetchMarketInsights(insightFilter === 'ALL' ? undefined : insightFilter)
      setInsights(updated)
    } catch (err: any) {
      addNotification(`Failed to approve insight: ${err.message}`, 'error')
    }
  }

  const handleRejectInsight = async (id: string) => {
    try {
      addNotification(`Rejecting insight ${id}...`, 'info')
      await rejectMarketInsight(id)
      addNotification('Insight rejected', 'warning')
      const updated = await fetchMarketInsights(insightFilter === 'ALL' ? undefined : insightFilter)
      setInsights(updated)
    } catch (err: any) {
      addNotification(`Failed to reject insight: ${err.message}`, 'error')
    }
  }

  const handleTriggerInsight = async () => {
    try {
      setIsGeneratingInsights(true)
      addNotification('Triggering AI Market Insight generator job...', 'info')
      const res = await triggerMarketInsightJob()
      addNotification(`Insight generator completed! Generated ${res.results?.length || 0} insight(s).`, 'success')
      const updated = await fetchMarketInsights(insightFilter === 'ALL' ? undefined : insightFilter)
      setInsights(updated)
    } catch (err: any) {
      addNotification(`Insight generator failed: ${err.message}`, 'error')
    } finally {
      setIsGeneratingInsights(false)
    }
  }

  const handleWalletAction = async (address: string, action: 'approve' | 'reject') => {
    try {
      addNotification(`Sending ${action} request for ${address.substring(0, 6)}...`, 'info')
      const res = await approveWallet(address, action)
      if (res.success) {
        approveWalletCandidate(address, action)
        addNotification(`Wallet candidate successfully ${action}d`, 'success')
        // Refresh candidates list from API to sync with backend state
        try {
          const cands = await fetchWalletCandidates()
          if (cands && cands.candidates) {
            setWalletCandidates(cands.candidates)
          }
        } catch (refreshErr) {
          console.warn('Could not refresh candidates list:', refreshErr)
        }
      }
    } catch (err) {
      console.error(err)
      addNotification(`Failed to ${action} candidate wallet`, 'error')
    }
  }

  const handleAddWallet = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newWalletAddress.trim()) {
      addNotification("Please enter a wallet address", "warning");
      return;
    }
    
    try {
      setIsAddingWallet(true);
      addNotification(`Adding wallet ${newWalletAddress.trim().substring(0, 6)}... to watchlist`, "info");
      const res = await addManualWallet(newWalletAddress.trim(), newWalletLabel.trim());
      
      if (res.success) {
        addNotification(res.message, "success");
        setNewWalletAddress('');
        setNewWalletLabel('');
        
        // Refresh active list
        try {
          const list = await fetchActiveWallets();
          if (list) setActiveWallets(list);
        } catch (listErr) {
          console.warn("Could not refresh active wallets:", listErr);
        }
      } else {
        addNotification(res.message, "warning");
      }
    } catch (err: any) {
      console.error(err);
      addNotification(err.message || "Failed to add manual wallet to watchlist", "error");
    } finally {
      setIsAddingWallet(false);
    }
  };

  const handleDeleteWallet = async (address: string) => {
    try {
      setIsDeletingWallet(address);
      addNotification(`Deactivating wallet ${address.substring(0, 6)}... from watchlist`, "info");
      const res = await deleteManualWallet(address);
      
      if (res.success) {
        addNotification(res.message, "success");
        
        // Refresh active list
        try {
          const list = await fetchActiveWallets();
          if (list) setActiveWallets(list);
        } catch (listErr) {
          console.warn("Could not refresh active wallets:", listErr);
        }
      }
    } catch (err: any) {
      console.error(err);
      addNotification(err.message || "Failed to deactivate wallet from watchlist", "error");
    } finally {
      setIsDeletingWallet(null);
    }
  };


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
            onClick={() => setActiveTab('portfolio')} 
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded transition-all cursor-pointer ${activeTab === 'portfolio' ? 'bg-indigo-600 text-white shadow' : 'text-cyber-textMuted hover:text-white'}`}
          >
            <Briefcase className="w-3.5 h-3.5" />
            <span>PORTFOLIO</span>
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
            onClick={() => setActiveTab('insights')} 
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded transition-all cursor-pointer ${activeTab === 'insights' ? 'bg-indigo-600 text-white shadow' : 'text-cyber-textMuted hover:text-white'}`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>AI INSIGHTS</span>
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

        {/* TAB 0: PORTFOLIO */}
        {activeTab === 'portfolio' && (() => {
          const solHolding = portfolio?.holdings?.find((h: any) => h.symbol === 'SOL');
          const wsolHolding = portfolio?.holdings?.find((h: any) => h.symbol === 'WSOL');
          const solPrice = solHolding?.price_usd || 77.34;
          const solAmount = solHolding?.amount || 0;
          const solValUsd = solAmount * solPrice;
          
          const wsolAmount = wsolHolding?.amount || 0;
          const wsolValUsd = wsolAmount * solPrice;

          // Select the correct history array based on the timeframe tab selection
          const history1d = portfolio?.history_1d || [];
          const history7d = portfolio?.history_7d || [];
          const history30d = portfolio?.history_30d || [];
          const history180d = portfolio?.history_180d || [];
          const history360d = portfolio?.history_360d || [];
          
          let historyToUse = history7d;
          if (timeframe === '1D') historyToUse = history1d;
          else if (timeframe === '30D') historyToUse = history30d;
          else if (timeframe === '180D') historyToUse = history180d;
          else if (timeframe === '360D') historyToUse = history360d;

          // Dynamic balance display during touch/scrubbing
          const activeValueUsd = hoveredPoint ? hoveredPoint.value_usd : (portfolio?.portfolio_value_usd || 0);
          const activeValueSol = hoveredPoint && hoveredPoint.sol_balance !== undefined
            ? hoveredPoint.sol_balance
            : solAmount;

          // Reconstruct paths for SVG using smooth cubic Bezier curve formulas
          let linePath = "M 50 100 L 550 100";
          let areaPath = "M 50 180 L 50 100 L 550 100 L 550 180 Z";
          let points: { x: number; y: number; val: number; ts: string; sol_balance?: number }[] = [];
          let highestPoint: any = null;
          let lowestPoint: any = null;
          
          if (historyToUse.length > 0) {
            const vals = historyToUse.map((h: any) => h.value_usd);
            const minVal = Math.min(...vals);
            const maxVal = Math.max(...vals);
            const valRange = maxVal - minVal;
            
            points = historyToUse.map((h: any, i: number) => {
              const x = historyToUse.length > 1
                ? 50 + (i * 500) / (historyToUse.length - 1)
                : 300;
              const y = valRange > 0 
                ? 160 - ((h.value_usd - minVal) * 120) / valRange 
                : 100;
              return { x, y, val: h.value_usd, ts: h.timestamp, sol_balance: h.sol_balance };
            });

            // Find highest & lowest points for Binance-grade labels
            highestPoint = points[0];
            lowestPoint = points[0];
            points.forEach((p: any) => {
              if (p.val > highestPoint.val) highestPoint = p;
              if (p.val < lowestPoint.val) lowestPoint = p;
            });

            // Quadratic Bezier interpolation for extra smooth curves
            if (points.length > 1) {
              let lPath = `M ${points[0].x} ${points[0].y}`;
              for (let i = 0; i < points.length - 1; i++) {
                const p0 = points[i];
                const p1 = points[i + 1];
                const cp1x = p0.x + (p1.x - p0.x) / 3;
                const cp1y = p0.y;
                const cp2x = p0.x + 2 * (p1.x - p0.x) / 3;
                const cp2y = p1.y;
                lPath += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p1.x} ${p1.y}`;
              }
              linePath = lPath;
              areaPath = `${lPath} L ${points[points.length - 1].x} 180 L ${points[0].x} 180 Z`;
            } else {
              linePath = `M 50 ${points[0].y} L 550 ${points[0].y}`;
              areaPath = `M 50 180 L 50 ${points[0].y} L 550 ${points[0].y} L 550 180 Z`;
            }
          }

          return (
            <div className="space-y-6 animate-fadeIn">
              {/* Top Stats Cards */}
              <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
                <div className="bg-cyber-card p-4 rounded-xl border border-cyber-border/60 relative overflow-hidden transition-all duration-300 hover:border-cyber-amber/30">
                  <div className="flex justify-between items-center">
                    <p className="text-xs text-cyber-textMuted font-mono uppercase">EST. TOTAL VALUE</p>
                    {hoveredPoint && (
                      <span className="text-[9px] font-mono font-bold text-cyber-amber bg-cyber-amber/10 border border-cyber-amber/20 px-1.5 py-0.5 rounded uppercase animate-pulse">
                        HISTORICAL
                      </span>
                    )}
                  </div>
                  <p className="text-2xl font-bold text-white mt-1.5 font-mono">
                    ${activeValueUsd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </p>
                  <p className="text-xs text-indigo-400 mt-1 font-mono">
                    ≈ {activeValueSol.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 4 })} SOL
                  </p>
                </div>
                <div className="bg-cyber-card p-4 rounded-xl border border-cyber-border/60">
                  <p className="text-xs text-cyber-textMuted font-mono uppercase">SOLANA BALANCE</p>
                  <p className="text-2xl font-bold text-indigo-400 mt-1.5 font-mono">
                    {solAmount.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 4 })} SOL
                  </p>
                  <p className="text-xs text-gray-400 mt-1 font-mono">
                    ≈ ${solValUsd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </p>
                </div>
                <div className="bg-cyber-card p-4 rounded-xl border border-cyber-border/60">
                  <p className="text-xs text-cyber-textMuted font-mono uppercase">WRAPPED SOL</p>
                  <p className="text-2xl font-bold text-indigo-400 mt-1.5 font-mono">
                    {wsolAmount.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 4 })} WSOL
                  </p>
                  <p className="text-xs text-gray-400 mt-1 font-mono">
                    ≈ ${wsolValUsd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </p>
                </div>
                <div className="bg-cyber-card p-4 rounded-xl border border-cyber-border/60">
                  <p className="text-xs text-cyber-textMuted font-mono uppercase">REALIZED PNL</p>
                  <p className={`text-2xl font-bold mt-1.5 font-mono ${(portfolio?.realized_pnl_usd ?? 0) >= 0 ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>
                    ${portfolio?.realized_pnl_usd ? portfolio.realized_pnl_usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '0.00'}
                  </p>
                </div>
                <div className="bg-cyber-card p-4 rounded-xl border border-cyber-border/60">
                  <p className="text-xs text-cyber-textMuted font-mono uppercase">UNREALIZED PNL</p>
                  <p className={`text-2xl font-bold mt-1.5 font-mono ${(portfolio?.unrealized_pnl_usd ?? 0) >= 0 ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>
                    ${portfolio?.unrealized_pnl_usd ? portfolio.unrealized_pnl_usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '0.00'}
                  </p>
                </div>
              </div>

              {/* Custom SVG Line Chart */}
              <div className="bg-cyber-card p-6 rounded-xl border border-cyber-border/60 space-y-4">
                <div className="flex justify-between items-center pb-3 border-b border-cyber-border/40">
                  <div>
                    <h3 className="text-sm font-semibold tracking-wider font-mono text-white">PNL GROWTH PERFORMANCE</h3>
                    {hoveredPoint ? (
                      <p className="text-[10px] text-cyber-amber font-mono font-bold mt-0.5 uppercase tracking-wider animate-pulse">
                        Selected: ${hoveredPoint.value_usd.toFixed(2)} USD ({new Date(hoveredPoint.timestamp).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })})
                      </p>
                    ) : (
                      <p className="text-[10px] text-cyber-textMuted font-mono mt-0.5">
                        REAL-TIME PORTFOLIO TRACKING • {timeframe} SAMPLES
                      </p>
                    )}
                  </div>
                  <span className="text-xs text-cyber-textMuted font-mono uppercase">EQUITY VALUE TREND</span>
                </div>
                
                <div className="h-64 relative flex items-center justify-center bg-cyber-cardLight/20 rounded-lg border border-cyber-border/30 overflow-hidden select-none">
                  {/* Glowing Background Lines */}
                  <div className="absolute inset-0 grid grid-cols-6 grid-rows-4 pointer-events-none opacity-10">
                    {Array.from({ length: 6 }).map((_, i) => (
                      <div key={i} className="border-r border-dashed border-white h-full" />
                    ))}
                    {Array.from({ length: 4 }).map((_, i) => (
                      <div key={i} className="border-b border-dashed border-white w-full" />
                    ))}
                  </div>

                  {/* SVG Area Chart */}
                  <svg 
                    className="w-full h-full p-4 overflow-visible cursor-crosshair" 
                    viewBox="0 0 600 200" 
                    preserveAspectRatio="none"
                    onMouseMove={(e) => handleSvgInteraction(e, historyToUse)}
                    onTouchMove={(e) => handleSvgInteraction(e, historyToUse)}
                    onMouseLeave={() => {
                      setHoveredPoint(null);
                      setHoveredPointIndex(null);
                    }}
                    onTouchEnd={() => {
                      setHoveredPoint(null);
                      setHoveredPointIndex(null);
                    }}
                  >
                    <defs>
                      <linearGradient id="chartGlow" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#4f46e5" stopOpacity="0.4" />
                        <stop offset="100%" stopColor="#4f46e5" stopOpacity="0.0" />
                      </linearGradient>
                    </defs>
                    
                    {/* Area */}
                    <path
                      d={areaPath}
                      fill="url(#chartGlow)"
                    />
                    
                    {/* Line */}
                    <path
                      d={linePath}
                      fill="none"
                      stroke="#818cf8"
                      strokeWidth="3"
                      className="drop-shadow-[0_0_8px_rgba(129,140,248,0.5)]"
                    />

                    {/* Highest & Lowest Annotations */}
                    {highestPoint && points.length > 1 && (
                      <g>
                        <circle cx={highestPoint.x} cy={highestPoint.y} r="4" fill="#10b981" />
                        <circle cx={highestPoint.x} cy={highestPoint.y} r="8" fill="none" stroke="#10b981" strokeWidth="1" className="animate-ping" />
                        <text
                          x={highestPoint.x}
                          y={highestPoint.y - 12}
                          textAnchor="middle"
                          fill="#10b981"
                          className="text-[9px] font-mono font-bold fill-cyber-emerald"
                        >
                          ▲ ${highestPoint.val.toFixed(2)}
                        </text>
                      </g>
                    )}

                    {lowestPoint && points.length > 1 && lowestPoint.val !== highestPoint.val && (
                      <g>
                        <circle cx={lowestPoint.x} cy={lowestPoint.y} r="4" fill="#ef4444" />
                        <circle cx={lowestPoint.x} cy={lowestPoint.y} r="8" fill="none" stroke="#ef4444" strokeWidth="1" className="animate-ping" />
                        <text
                          x={lowestPoint.x}
                          y={lowestPoint.y + 16}
                          textAnchor="middle"
                          fill="#ef4444"
                          className="text-[9px] font-mono font-bold fill-cyber-rose"
                        >
                          ▼ ${lowestPoint.val.toFixed(2)}
                        </text>
                      </g>
                    )}
                    
                    {/* Interactive Scrubbing Dashed Line and Hover Dot */}
                    {hoveredPointIndex !== null && points[hoveredPointIndex] && (
                      <g>
                        <line 
                          x1={points[hoveredPointIndex].x} 
                          y1={10} 
                          x2={points[hoveredPointIndex].x} 
                          y2={190} 
                          stroke="#818cf8" 
                          strokeDasharray="3 3" 
                          strokeWidth="1.5" 
                        />
                        <circle 
                          cx={points[hoveredPointIndex].x} 
                          cy={points[hoveredPointIndex].y} 
                          r="6" 
                          fill="#facc15" 
                          stroke="#818cf8" 
                          strokeWidth="2.5" 
                          className="shadow-lg"
                        />
                      </g>
                    )}
                  </svg>

                  {/* Left / Start Timestamp */}
                  <div className="absolute bottom-2 left-4 text-[9px] font-mono text-cyber-textMuted">
                    {historyToUse.length > 0 
                      ? new Date(historyToUse[0].timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) 
                      : 'START'}
                  </div>
                  {/* Right / End Timestamp */}
                  <div className="absolute bottom-2 right-4 text-[9px] font-mono text-cyber-textMuted">
                    {historyToUse.length > 0 
                      ? new Date(historyToUse[historyToUse.length - 1].timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + ' (CURRENT)'
                      : 'END'}
                  </div>
                </div>

                {/* Binance-style Timeframe Selector tabs */}
                <div className="flex justify-center space-x-2 pt-2 border-t border-cyber-border/20">
                  {(['1D', '7D', '30D', '180D', '360D'] as const).map((tf) => (
                    <button
                      key={tf}
                      onClick={() => {
                        setTimeframe(tf);
                        setHoveredPoint(null);
                        setHoveredPointIndex(null);
                      }}
                      className={`px-4 py-1.5 rounded-full text-xs font-bold font-mono transition-all cursor-pointer ${
                        timeframe === tf
                          ? 'bg-cyber-amber/10 text-cyber-amber border border-cyber-amber/30 shadow-[0_0_8px_rgba(245,158,11,0.2)]'
                          : 'text-cyber-textMuted hover:text-white border border-transparent'
                      }`}
                    >
                      {tf}
                    </button>
                  ))}
                </div>
              </div>

            {/* SPL Holdings Table */}
            <div className="bg-cyber-card p-6 rounded-xl border border-cyber-border/60">
              <div className="flex justify-between items-center pb-3 border-b border-cyber-border/40 mb-4">
                <h3 className="text-sm font-semibold tracking-wider font-mono text-white">SPL TOKEN HOLDINGS</h3>
                <span className="text-xs text-cyber-textMuted font-mono">ON-CHAIN SPL BALANCE</span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left font-mono text-xs border-collapse">
                  <thead>
                    <tr className="text-cyber-textMuted border-b border-cyber-border/30 pb-2">
                      <th className="py-2.5">ASSET</th>
                      <th className="py-2.5">BALANCE</th>
                      <th className="py-2.5">MARKET PRICE</th>
                      <th className="py-2.5">COST BASIS</th>
                      <th className="py-2.5">VALUE (USD)</th>
                      <th className="py-2.5">UNREALIZED PNL</th>
                      <th className="py-2.5">ALLOCATION</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolio?.holdings && portfolio.holdings.length > 0 ? (
                      portfolio.holdings.map((hold: any) => {
                        const isPos = hold.unrealized_pnl_usd >= 0;
                        const allocPct = portfolio.portfolio_value_usd > 0 
                          ? (hold.value_usd / portfolio.portfolio_value_usd) * 100 
                          : 0;
                        return (
                          <tr key={hold.mint} className="border-b border-cyber-border/20 hover:bg-cyber-cardLight/30 transition-colors">
                            <td className="py-3">
                              <span className="font-bold text-white block">{hold.symbol}</span>
                              <span className="text-[10px] text-cyber-textMuted font-mono block max-w-[120px] truncate">{hold.name}</span>
                            </td>
                            <td className="py-3 font-semibold text-gray-200">
                              {hold.amount.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                            </td>
                            <td className="py-3 text-gray-300">
                              ${hold.price_usd.toLocaleString(undefined, { minimumFractionDigits: 6, maximumFractionDigits: 6 })}
                            </td>
                            <td className="py-3 text-gray-400">
                              {hold.cost_basis > 0 
                                ? `$${hold.cost_basis.toLocaleString(undefined, { minimumFractionDigits: 6, maximumFractionDigits: 6 })}` 
                                : 'Airdrop/Ext'}
                            </td>
                            <td className="py-3 font-bold text-white">
                              ${hold.value_usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                            </td>
                            <td className={`py-3 font-bold ${isPos ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>
                              {isPos ? '+' : ''}${hold.unrealized_pnl_usd.toFixed(2)}
                              <span className="text-[10px] block opacity-85 font-semibold">
                                ({isPos ? '+' : ''}{(hold.unrealized_pnl_pct * 100).toFixed(2)}%)
                              </span>
                            </td>
                            <td className="py-3">
                              <div className="flex items-center space-x-2">
                                <div className="w-16 bg-cyber-cardLight h-1.5 rounded-full overflow-hidden">
                                  <div 
                                    className="bg-indigo-500 h-full" 
                                    style={{ width: `${Math.min(allocPct, 100)}%` }}
                                  />
                                </div>
                                <span className="text-gray-300 text-[10px]">{allocPct.toFixed(1)}%</span>
                              </div>
                            </td>
                          </tr>
                        );
                      })
                    ) : (
                      <tr>
                        <td colSpan={7} className="text-center py-6 text-cyber-textMuted font-mono">
                          No active SPL holdings detected in loaded wallet address.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* PDF Report Export Component */}
            <div className="bg-cyber-card p-6 rounded-xl border border-cyber-border/60 space-y-4">
              <div className="flex justify-between items-center pb-3 border-b border-cyber-border/40">
                <div className="flex items-center space-x-2">
                  <FileText className="w-5 h-5 text-cyber-amber" />
                  <h3 className="text-sm font-semibold tracking-wider font-mono text-white">TRANSACTION REPORT EXPORT (PDF)</h3>
                </div>
                <span className="text-xs text-cyber-textMuted font-mono">EXPORT FILTERED TRANSACTION LOGS</span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
                {/* Start Date */}
                <div className="space-y-1.5">
                  <label className="text-[10px] text-cyber-textMuted font-mono uppercase block">START DATE</label>
                  <input
                    type="date"
                    value={pdfStartDate}
                    onChange={(e) => setPdfStartDate(e.target.value)}
                    className="w-full bg-cyber-cardLight border border-cyber-border/60 rounded px-3 py-2 text-white font-mono text-xs focus:outline-none focus:border-indigo-500 transition-colors"
                  />
                </div>

                {/* End Date */}
                <div className="space-y-1.5">
                  <label className="text-[10px] text-cyber-textMuted font-mono uppercase block">END DATE</label>
                  <input
                    type="date"
                    value={pdfEndDate}
                    onChange={(e) => setPdfEndDate(e.target.value)}
                    className="w-full bg-cyber-cardLight border border-cyber-border/60 rounded px-3 py-2 text-white font-mono text-xs focus:outline-none focus:border-indigo-500 transition-colors"
                  />
                </div>

                {/* Quick Selection Filters & Export Button */}
                <div className="flex space-x-2">
                  <button
                    onClick={() => {
                      const now = new Date();
                      setPdfEndDate(now.toISOString().split('T')[0]);
                      
                      // Today quick filter
                      const today = new Date();
                      setPdfStartDate(today.toISOString().split('T')[0]);
                    }}
                    className="flex-1 bg-cyber-cardLight/50 hover:bg-cyber-cardLight border border-cyber-border/40 hover:border-cyber-border text-white text-xs font-mono py-2 rounded transition-all cursor-pointer text-center"
                  >
                    TODAY
                  </button>
                  <button
                    onClick={() => {
                      const now = new Date();
                      setPdfEndDate(now.toISOString().split('T')[0]);
                      
                      // 7 Days Ago
                      const sevenDaysAgo = new Date();
                      sevenDaysAgo.setDate(now.getDate() - 7);
                      setPdfStartDate(sevenDaysAgo.toISOString().split('T')[0]);
                    }}
                    className="flex-1 bg-cyber-cardLight/50 hover:bg-cyber-cardLight border border-cyber-border/40 hover:border-cyber-border text-white text-xs font-mono py-2 rounded transition-all cursor-pointer text-center"
                  >
                    7D
                  </button>
                  <button
                    onClick={() => {
                      const now = new Date();
                      setPdfEndDate(now.toISOString().split('T')[0]);
                      
                      // 30 Days Ago
                      const thirtyDaysAgo = new Date();
                      thirtyDaysAgo.setDate(now.getDate() - 30);
                      setPdfStartDate(thirtyDaysAgo.toISOString().split('T')[0]);
                    }}
                    className="flex-1 bg-cyber-cardLight/50 hover:bg-cyber-cardLight border border-cyber-border/40 hover:border-cyber-border text-white text-xs font-mono py-2 rounded transition-all cursor-pointer text-center"
                  >
                    30D
                  </button>
                  <button
                    onClick={() => {
                      setPdfStartDate('');
                      setPdfEndDate('');
                    }}
                    className="flex-1 bg-cyber-rose/10 hover:bg-cyber-rose/25 border border-cyber-rose/30 text-cyber-rose text-xs font-mono py-2 rounded transition-all cursor-pointer text-center"
                  >
                    RESET
                  </button>
                </div>
              </div>

              {/* Action Export Button */}
              <div className="flex justify-end pt-2">
                <button
                  disabled={isExportingPdf}
                  onClick={async () => {
                    try {
                      setIsExportingPdf(true);
                      
                      // Convert local YYYY-MM-DD input date to ISO timestamps
                      let startIso: string | undefined = undefined;
                      let endIso: string | undefined = undefined;
                      
                      if (pdfStartDate) {
                        startIso = new Date(pdfStartDate + 'T00:00:00Z').toISOString();
                      }
                      if (pdfEndDate) {
                        endIso = new Date(pdfEndDate + 'T23:59:59Z').toISOString();
                      }
                      
                      const url = exportPortfolioPdfUrl(startIso, endIso);
                      
                      // Open PDF in new tab or trigger direct download
                      window.open(url, '_blank');
                      
                      addNotification('Generating transaction report PDF. Check your downloads.', 'info');
                    } catch (err: any) {
                      console.error('PDF export failed:', err);
                      addNotification(`Failed to export PDF: ${err.message || err}`, 'error');
                    } finally {
                      setIsExportingPdf(false);
                    }
                  }}
                  className={`flex items-center space-x-2 px-5 py-2.5 rounded-lg text-xs font-bold font-mono tracking-wider transition-all cursor-pointer ${
                    isExportingPdf
                      ? 'bg-cyber-cardLight text-cyber-textMuted border border-cyber-border'
                      : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-[0_0_15px_rgba(99,102,241,0.4)] hover:shadow-[0_0_20px_rgba(99,102,241,0.6)] border border-indigo-500/50'
                  }`}
                >
                  {isExportingPdf ? (
                    <>
                      <RotateCw className="w-4 h-4 animate-spin" />
                      <span>GENERATING REPORT...</span>
                    </>
                  ) : (
                    <>
                      <Download className="w-4 h-4" />
                      <span>EXPORT PDF REPORT</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        );
      })()}

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
                <div className="max-h-[320px] overflow-y-auto pr-1.5 space-y-3">
                  {activeWallets.length > 0 ? (
                    activeWallets.map((wallet) => (
                      <div key={wallet.wallet_address} className="bg-cyber-cardLight/40 rounded-lg p-3 border border-cyber-border/40">
                        <div className="flex justify-between items-center mb-1">
                          <span className="text-xs font-semibold text-gray-300 font-mono">{wallet.label || 'Whale Wallet'}</span>
                          <span className={`h-2 w-2 rounded-full ${wallet.active ? 'bg-cyber-emerald shadow-[0_0_8px_#10B981]' : 'bg-cyber-rose shadow-[0_0_8px_#F43F5E]'}`}></span>
                        </div>
                        <p className="text-[10px] text-cyber-textMuted font-mono truncate" title={wallet.wallet_address}>{wallet.wallet_address}</p>
                        <div className="mt-2 flex justify-between items-center text-xs">
                          <span className="text-cyber-textMuted">Source:</span>
                          <span className="font-mono text-gray-200 uppercase">{wallet.source}</span>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="text-xs text-cyber-textMuted font-mono text-center py-4">No active wallets loaded.</p>
                  )}
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

            <section className="lg:col-span-1 flex flex-col p-4 bg-cyber-card border border-cyber-border rounded-xl space-y-4">
              <div className="flex items-center space-x-2 pb-3 border-b border-cyber-border/40">
                <Compass className="w-5 h-5 text-indigo-400" />
                <h2 className="text-sm font-semibold tracking-wider text-white font-mono">ACTIVE TARGETS</h2>
              </div>
              
              {/* Manual Add Wallet Form */}
              <form onSubmit={handleAddWallet} className="bg-cyber-cardLight/30 border border-cyber-border/40 rounded-lg p-3 space-y-2">
                <span className="text-[10px] text-cyber-textMuted font-mono uppercase block">Add Manual Whale Wallet</span>
                <div className="space-y-2">
                  <input
                    type="text"
                    placeholder="Solana Wallet Address"
                    value={newWalletAddress}
                    onChange={(e) => setNewWalletAddress(e.target.value)}
                    className="w-full bg-cyber-cardLight border border-cyber-border/60 rounded px-2.5 py-1.5 text-white font-mono text-xs focus:outline-none focus:border-indigo-500 transition-colors"
                  />
                  <input
                    type="text"
                    placeholder="Label / Name (optional)"
                    value={newWalletLabel}
                    onChange={(e) => setNewWalletLabel(e.target.value)}
                    className="w-full bg-cyber-cardLight border border-cyber-border/60 rounded px-2.5 py-1.5 text-white font-mono text-xs focus:outline-none focus:border-indigo-500 transition-colors"
                  />
                  <button
                    type="submit"
                    disabled={isAddingWallet}
                    className="w-full flex items-center justify-center space-x-1 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-cyber-cardLight text-white rounded font-bold font-mono text-xs transition-colors cursor-pointer border border-indigo-500/50"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>{isAddingWallet ? 'ADDING...' : 'ADD WALLET'}</span>
                  </button>
                </div>
              </form>

              <div className="max-h-[400px] overflow-y-auto pr-1 space-y-3">
                {activeWallets.length > 0 ? (
                  activeWallets.map((wallet) => (
                    <div key={wallet.wallet_address} className="p-3.5 rounded bg-cyber-cardLight border border-cyber-border/40 flex justify-between items-start">
                      <div className="space-y-1 select-text">
                        <p className="text-xs font-bold text-gray-300 font-mono">{wallet.label || 'Whale Target'}</p>
                        <p className="text-[10px] text-cyber-textMuted font-mono break-all">{wallet.wallet_address}</p>
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-[8px] font-bold bg-cyber-emerald/10 text-cyber-emerald border border-cyber-emerald/20 font-mono uppercase">
                          {wallet.source === 'manual' ? 'MANUAL TARGET' : 'DYNAMIC TARGET'}
                        </span>
                      </div>
                      <button
                        onClick={() => handleDeleteWallet(wallet.wallet_address)}
                        disabled={isDeletingWallet === wallet.wallet_address}
                        className="ml-2 p-1.5 bg-cyber-rose/10 hover:bg-cyber-rose/30 border border-cyber-rose/30 hover:border-cyber-rose/60 text-cyber-rose rounded transition-all cursor-pointer flex-shrink-0"
                        title="Remove Wallet"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))
                ) : (
                  <p className="text-xs text-cyber-textMuted font-mono text-center py-4">No active watchlist wallets.</p>
                )}
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
                        <td className="py-3 px-3 text-cyber-textMuted">{trade.holdingTime ?? '-- min'}</td>
                        <td className="py-3 px-3">
                          <span className="text-[10px] bg-cyber-cardLight px-2 py-0.5 rounded border border-cyber-border text-gray-200">
                            {trade.exitReason ?? 'unknown'}
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

        {/* TAB 4.5: AI MARKET INSIGHTS */}
        {activeTab === 'insights' && (
          <section className="flex flex-col space-y-4">
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center p-4 bg-cyber-card border border-cyber-border rounded-xl gap-4">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-indigo-600/20 text-indigo-400 rounded-lg border border-indigo-500/30">
                  <Sparkles className="w-5 h-5 animate-pulse" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold tracking-wider text-white font-mono uppercase">F-02 AI Market Insights Pipeline (Statistical Gated)</h2>
                  <p className="text-xs text-cyber-textMuted font-mono">LLM Hypotheses verified against real historical ClosedTrade statistics before human approval checkpoint</p>
                </div>
              </div>

              <div className="flex items-center space-x-3">
                <div className="flex bg-cyber-cardLight p-1 rounded-lg border border-cyber-border text-xs font-mono">
                  {['ALL', 'PENDING_REVIEW', 'APPROVED', 'REJECTED_STATISTICAL', 'REJECTED_MANUAL'].map((st) => (
                    <button
                      key={st}
                      onClick={() => setInsightFilter(st)}
                      className={`px-2.5 py-1 rounded transition-colors cursor-pointer ${insightFilter === st ? 'bg-indigo-600 text-white font-bold' : 'text-cyber-textMuted hover:text-white'}`}
                    >
                      {st.replace('_', ' ')}
                    </button>
                  ))}
                </div>

                <button
                  onClick={handleTriggerInsight}
                  disabled={isGeneratingInsights}
                  className="flex items-center space-x-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-xs font-mono font-bold transition-all cursor-pointer shadow-lg"
                >
                  <RotateCw className={`w-3.5 h-3.5 ${isGeneratingInsights ? 'animate-spin' : ''}`} />
                  <span>{isGeneratingInsights ? 'GENERATING...' : 'TRIGGER JOB'}</span>
                </button>
              </div>
            </div>

            {/* Insights List Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {insights.length > 0 ? (
                insights.map((insight: any) => (
                  <div key={insight.insight_id} className="p-5 bg-cyber-card border border-cyber-border rounded-xl space-y-3 relative overflow-hidden">
                    <div className="flex justify-between items-start">
                      <span className={`px-2.5 py-1 rounded text-[10px] font-mono font-bold ${
                        insight.statistical_status === 'APPROVED' ? 'bg-cyber-emerald/20 text-cyber-emerald border border-cyber-emerald/40' :
                        insight.statistical_status === 'PENDING_REVIEW' ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 animate-pulse' :
                        insight.statistical_status === 'REJECTED_STATISTICAL' ? 'bg-cyber-amber/10 text-cyber-amber border border-cyber-amber/30' :
                        'bg-cyber-rose/10 text-cyber-rose border border-cyber-rose/30'
                      }`}>
                        {insight.statistical_status}
                      </span>
                      <span className="text-[10px] font-mono text-cyber-textMuted/60">{new Date(insight.created_at).toLocaleString()}</span>
                    </div>

                    <h3 className="text-sm font-bold text-white font-mono leading-snug">{insight.hypothesis_text}</h3>

                    <div className="bg-cyber-cardLight/50 p-2.5 rounded border border-cyber-border/40 font-mono text-xs space-y-1">
                      <div className="text-[10px] text-cyber-textMuted uppercase font-bold">Tested Condition:</div>
                      <code className="text-indigo-300 text-xs break-all block bg-cyber-card p-1 rounded border border-cyber-border/20">{insight.affected_condition}</code>
                    </div>

                    <div className="grid grid-cols-3 gap-2 font-mono text-xs pt-1">
                      <div className="bg-cyber-cardLight p-2 rounded text-center border border-cyber-border/20">
                        <div className="text-[9px] text-cyber-textMuted uppercase">Group Samples</div>
                        <div className="text-white font-bold">{insight.sample_size_group_a} vs {insight.sample_size_group_b}</div>
                      </div>
                      <div className="bg-cyber-cardLight p-2 rounded text-center border border-cyber-border/20">
                        <div className="text-[9px] text-cyber-textMuted uppercase">Win Rate Diff</div>
                        <div className={`font-bold ${insight.win_rate_diff > 0 ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>
                          {insight.win_rate_diff > 0 ? '+' : ''}{(insight.win_rate_diff * 100).toFixed(1)}%
                        </div>
                      </div>
                      <div className="bg-cyber-cardLight p-2 rounded text-center border border-cyber-border/20">
                        <div className="text-[9px] text-cyber-textMuted uppercase">Expectancy Diff</div>
                        <div className={`font-bold ${insight.expectancy_diff > 0 ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>
                          {insight.expectancy_diff > 0 ? '+' : ''}{insight.expectancy_diff.toFixed(2)}R
                        </div>
                      </div>
                    </div>

                    {insight.rejection_reason && (
                      <div className="p-2 bg-cyber-rose/10 border border-cyber-rose/20 rounded text-[11px] font-mono text-cyber-rose">
                        <span className="font-bold">Rejection Reason: </span>{insight.rejection_reason}
                      </div>
                    )}

                    {insight.statistical_status === 'PENDING_REVIEW' && (
                      <div className="flex space-x-3 pt-2">
                        <button
                          onClick={() => handleApproveInsight(insight.insight_id)}
                          className="flex-1 py-1.5 bg-cyber-emerald/20 hover:bg-cyber-emerald text-cyber-emerald hover:text-white rounded border border-cyber-emerald/40 text-xs font-mono font-bold transition-all cursor-pointer"
                        >
                          Approve (Retrain Candidate)
                        </button>
                        <button
                          onClick={() => handleRejectInsight(insight.insight_id)}
                          className="flex-1 py-1.5 bg-cyber-rose/20 hover:bg-cyber-rose text-cyber-rose hover:text-white rounded border border-cyber-rose/40 text-xs font-mono font-bold transition-all cursor-pointer"
                        >
                          Reject Hypothesis
                        </button>
                      </div>
                    )}
                  </div>
                ))
              ) : (
                <div className="col-span-2 p-12 text-center bg-cyber-card border border-cyber-border rounded-xl font-mono text-cyber-textMuted">
                  <Sparkles className="w-8 h-8 text-cyber-textMuted/40 mx-auto mb-2" />
                  <p className="text-sm">No market insights found for status filter '{insightFilter}'.</p>
                  <p className="text-xs text-cyber-textMuted/60 mt-1">Click "TRIGGER JOB" above to execute LLM hypothesis generation and statistical validation.</p>
                </div>
              )}
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
              <div className="border-b border-cyber-border/20 pb-2 space-y-1">
                <div className="grid grid-cols-2 gap-2">
                  <span className="text-cyber-textMuted">Token Symbol:</span>
                  <span className="font-bold text-indigo-400">
                    {selectedSignal.token_symbol || 'N/A (No Metadata)'}
                  </span>
                </div>
                {selectedSignal.token_name && selectedSignal.token_name !== selectedSignal.token_symbol && (
                  <div className="grid grid-cols-2 gap-2">
                    <span className="text-cyber-textMuted">Token Name:</span>
                    <span className="text-gray-300">{selectedSignal.token_name}</span>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <span className="text-cyber-textMuted">Full Address:</span>
                  <span className="text-gray-300 break-all select-all bg-cyber-cardLight p-1 rounded border border-cyber-border/20">
                    {selectedSignal.token_address || selectedSignal.token}
                  </span>
                </div>
                {selectedSignal.token_address && (
                  <div className="grid grid-cols-2 gap-2 pt-1">
                    <span className="text-cyber-textMuted">DEX Link:</span>
                    <span>
                      <a
                        href={
                          selectedSignal.token_address.endsWith('pump')
                            ? `https://pump.fun/${selectedSignal.token_address}`
                            : `https://dexscreener.com/solana/${selectedSignal.token_address}`
                        }
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-300 underline hover:text-indigo-100 font-bold"
                      >
                        {selectedSignal.token_address.endsWith('pump') ? 'Open on Pump.fun 💊' : 'Open on DexScreener 📈'}
                      </a>
                    </span>
                  </div>
                )}
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">Confidence:</span>
                <span className="font-bold text-white">{selectedSignal.confidence}%</span>
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">Direction:</span>
                <span className={`font-bold ${selectedSignal.direction === 'BUY' ? 'text-cyber-emerald' : selectedSignal.direction === 'SELL' ? 'text-cyber-rose' : 'text-gray-400'}`}>
                  {selectedSignal.direction || 'HOLD'}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-cyber-border/20 pb-2">
                <span className="text-cyber-textMuted">Safety Status:</span>
                <span className={`font-bold ${selectedSignal.safetyPassed ? 'text-cyber-emerald' : 'text-cyber-rose'}`}>
                  {selectedSignal.safetyPassed ? 'PASSED' : 'BLOCKED'}
                </span>
              </div>
              <div>
                <p className="text-xs font-bold text-gray-300 mb-1.5">ML Feature Input Vectors:</p>
                <div className="bg-cyber-cardLight p-3 rounded border border-cyber-border/40 space-y-1 text-[10px] text-cyber-textMuted">
                  <div>• position_size_usd: <span className="text-gray-300">
                    {selectedSignal.features?.position_size_usd !== undefined 
                      ? selectedSignal.features.position_size_usd.toFixed(2) 
                      : '150.00'}
                  </span></div>
                  <div>• token_age_minutes: <span className="text-gray-300">
                    {selectedSignal.features?.token_age_minutes !== undefined 
                      ? selectedSignal.features.token_age_minutes.toFixed(1) 
                      : '120.00'}
                  </span> <span className="text-cyber-amber/60">(on-chain)</span></div>
                  <div>• liquidity_pool_depth: <span className="text-gray-300">
                    {selectedSignal.features?.liquidity_pool_depth !== undefined 
                      ? selectedSignal.features.liquidity_pool_depth.toLocaleString(undefined, { maximumFractionDigits: 2 }) 
                      : '15,000.00'}
                  </span> <span className="text-cyber-amber/60">(on-chain)</span></div>
                  <div>• slippage_actual: <span className="text-gray-300">
                    {selectedSignal.features?.slippage_actual !== undefined && selectedSignal.features.slippage_actual !== null 
                      ? selectedSignal.features.slippage_actual.toFixed(3) 
                      : '0.012'}
                  </span></div>
                  <div>• win_rate_30d: <span className="text-gray-300">
                    {selectedSignal.features?.win_rate_30d !== undefined 
                      ? selectedSignal.features.win_rate_30d.toFixed(2) 
                      : '0.55'}
                  </span> <span className="text-cyber-textMuted/60">(prior / SQLite)</span></div>
                  <div>• avg_holding_time_minutes: <span className="text-gray-300">
                    {selectedSignal.features?.avg_holding_time_minutes !== undefined 
                      ? selectedSignal.features.avg_holding_time_minutes.toFixed(1) 
                      : '15.0'}
                  </span> <span className="text-cyber-textMuted/60">(prior / SQLite)</span></div>
                  <div>• sol_usd_momentum: <span className="text-gray-300">
                    {selectedSignal.features?.sol_usd_momentum !== undefined 
                      ? selectedSignal.features.sol_usd_momentum.toFixed(3) 
                      : '0.024'}
                  </span></div>
                  <div>• cluster_score: <span className="text-gray-300">
                    {selectedSignal.features?.cluster_score !== undefined 
                      ? selectedSignal.features.cluster_score.toFixed(1)
                      : '0.0'}
                  </span> <span className="text-cyber-textMuted/60">(AND mode boost)</span></div>
                </div>
                <p className="text-[9px] text-cyber-textMuted/50 mt-1">
                  <span className="text-cyber-amber/60">on-chain</span> = real DexScreener data · 
                  <span className="text-cyber-textMuted/60"> prior/SQLite</span> = historical average (no real trades yet)
                </p>
              </div>
            </div>
            <div className="flex justify-between items-center pt-3">
              {selectedSignal.token_address && (
                <a
                  href={
                    selectedSignal.token_address.endsWith('pump')
                      ? `https://pump.fun/${selectedSignal.token_address}`
                      : `https://dexscreener.com/solana/${selectedSignal.token_address}`
                  }
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1.5 bg-indigo-900/50 border border-indigo-500/30 text-indigo-300 hover:bg-indigo-700 rounded text-xs font-mono transition-colors font-bold"
                >
                  {selectedSignal.token_address.endsWith('pump') ? 'Open on Pump.fun 💊' : 'Open on DexScreener 📈'}
                </a>
              )}
              <button onClick={() => setSelectedSignal(null)} className="px-4 py-1.5 bg-indigo-600 text-white rounded text-xs font-mono font-bold cursor-pointer ml-auto">
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
