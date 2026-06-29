import { create } from 'zustand'

export interface WhaleWalletState {
  active: boolean;
  txCount: string;
}

export interface WalletMonitorState {
  whaleA: WhaleWalletState;
  whaleB: WhaleWalletState;
  triggerWindow: string;
  lastTrigger: string;
  mlModel: string;
  nextRetrain: string;
  accuracy: string;
}

export interface LiveSignal {
  id: string;
  direction: 'BUY' | 'SELL' | 'HOLD';
  token: string;
  confidence: number;
  timestamp: string;
  details: string;
  safetyPassed: boolean;
}

export interface TradeLogEntry {
  id: string;
  direction: 'BUY' | 'SELL' | 'HOLD';
  token: string;
  pnl: string; // e.g. "+12%" or "-2%"
  isPositive: boolean;
}

export interface ConfidenceHistoryPoint {
  id: string;
  timestamp: string;
  score: number;
}

export interface MetricsState {
  winRate: string;
  triggersToday: string;
  alertsFiredCount: string;
  alertsFiredTotal: string;
}

export interface ComponentStatus {
  name: string;
  status: 'running' | 'error' | 'idle';
  detail?: string;
}

export interface SystemStatus {
  overall_status: 'healthy' | 'degraded' | 'error';
  rpc_status: 'online' | 'offline' | 'simulation';
  components: ComponentStatus[];
  timestamp: string;
}

export interface WalletCandidate {
  wallet_address: string;
  wallet_short: string;
  label: string;
  source: string;
  discovery_reason: string;
  discovered_at: string;
  status: 'pending' | 'approved' | 'rejected';
}

export interface ToastNotification {
  id: string;
  type: 'info' | 'success' | 'warning' | 'error';
  message: string;
  timestamp: string;
}

interface AppStore {
  isConnected: boolean;
  walletMonitor: WalletMonitorState;
  liveSignals: LiveSignal[];
  confidenceGaugeScore: number | null;
  confidenceThreshold: number;
  confidenceHistory: ConfidenceHistoryPoint[];
  tradeLog: TradeLogEntry[];
  metrics: MetricsState;
  systemStatus: SystemStatus;
  walletCandidates: WalletCandidate[];
  notifications: ToastNotification[];
  activeTab: 'overview' | 'watchlist' | 'signals' | 'trades' | 'diagnostics';
  selectedSignal: any | null;
  selectedTrade: any | null;
  selectedCandidate: any | null;
  selectedError: any | null;
  errorLogs: any[];
  
  // Actions
  setConnected: (connected: boolean) => void;
  updateWalletMonitor: (data: Partial<WalletMonitorState>) => void;
  addSignal: (signal: LiveSignal) => void;
  setConfidenceGaugeScore: (score: number | null) => void;
  setConfidenceHistory: (points: ConfidenceHistoryPoint[]) => void;
  addTrade: (trade: TradeLogEntry) => void;
  updateMetrics: (metrics: Partial<MetricsState>) => void;
  setSystemStatus: (status: SystemStatus) => void;
  setWalletCandidates: (candidates: WalletCandidate[]) => void;
  addWalletCandidate: (candidate: WalletCandidate) => void;
  approveWalletCandidate: (address: string, action: 'approve' | 'reject') => void;
  addNotification: (message: string, type?: ToastNotification['type']) => void;
  dismissNotification: (id: string) => void;
  setActiveTab: (tab: 'overview' | 'watchlist' | 'signals' | 'trades' | 'diagnostics') => void;
  setSelectedSignal: (signal: any | null) => void;
  setSelectedTrade: (trade: any | null) => void;
  setSelectedCandidate: (candidate: any | null) => void;
  setSelectedError: (error: any | null) => void;
  setErrorLogs: (logs: any[]) => void;
}

export const useStore = create<AppStore>()((set) => ({
  isConnected: false,
  walletMonitor: {
    whaleA: { active: false, txCount: "--" },
    whaleB: { active: false, txCount: "--" },
    triggerWindow: "--",
    lastTrigger: "--",
    mlModel: "--",
    nextRetrain: "--",
    accuracy: "--"
  },
  liveSignals: [],
  confidenceGaugeScore: null,
  confidenceThreshold: 75,
  confidenceHistory: [],
  tradeLog: [],
  metrics: {
    winRate: "--",
    triggersToday: "--",
    alertsFiredCount: "--",
    alertsFiredTotal: "--"
  },
  systemStatus: {
    overall_status: 'healthy',
    rpc_status: 'simulation',
    components: [],
    timestamp: new Date().toISOString()
  },
  walletCandidates: [],
  notifications: [],
  activeTab: 'overview',
  selectedSignal: null,
  selectedTrade: null,
  selectedCandidate: null,
  selectedError: null,
  errorLogs: [],

  setConnected: (connected) => set({ isConnected: connected }),
  
  updateWalletMonitor: (data) => set((state) => ({
    walletMonitor: { ...state.walletMonitor, ...data }
  })),
  
  addSignal: (signal) => set((state) => {
    // Also build a point for confidence history from signals
    const newPoint: ConfidenceHistoryPoint = {
      id: signal.id,
      timestamp: signal.timestamp,
      score: signal.confidence
    };
    const history = [...state.confidenceHistory, newPoint].slice(-15);
    return {
      liveSignals: [signal, ...state.liveSignals].slice(0, 50),
      confidenceHistory: history
    }
  }),
  
  setConfidenceGaugeScore: (score) => set({ confidenceGaugeScore: score }),
  
  setConfidenceHistory: (points) => set({ confidenceHistory: points }),
  
  addTrade: (trade) => set((state) => ({
    tradeLog: [trade, ...state.tradeLog].slice(0, 50)
  })),
  
  updateMetrics: (newMetrics) => set((state) => ({
    metrics: { ...state.metrics, ...newMetrics }
  })),

  setSystemStatus: (status) => set({ systemStatus: status }),

  setWalletCandidates: (candidates) => set({ walletCandidates: candidates }),

  addWalletCandidate: (candidate) => set((state) => {
    // Avoid duplicate candidate listings
    if (state.walletCandidates.some(c => c.wallet_address === candidate.wallet_address)) {
      return {};
    }
    return { walletCandidates: [...state.walletCandidates, candidate] };
  }),

  approveWalletCandidate: (address, action) => set((state) => ({
    walletCandidates: state.walletCandidates.map(c => 
      c.wallet_address === address ? { ...c, status: action === 'approve' ? 'approved' : 'rejected' } : c
    )
  })),

  addNotification: (message, type = 'info') => set((state) => {
    const id = `toast_${Math.random().toString(36).substr(2, 9)}`;
    const newNotification: ToastNotification = {
      id,
      type,
      message,
      timestamp: new Date().toLocaleTimeString()
    };
    return {
      notifications: [newNotification, ...state.notifications].slice(0, 5)
    };
  }),

  dismissNotification: (id) => set((state) => ({
    notifications: state.notifications.filter(n => n.id !== id)
  })),

  setActiveTab: (tab) => set({ activeTab: tab }),
  setSelectedSignal: (signal) => set({ selectedSignal: signal }),
  setSelectedTrade: (trade) => set({ selectedTrade: trade }),
  setSelectedCandidate: (candidate) => set({ selectedCandidate: candidate }),
  setSelectedError: (error) => set({ selectedError: error }),
  setErrorLogs: (logs) => set({ errorLogs: logs }),
}))
