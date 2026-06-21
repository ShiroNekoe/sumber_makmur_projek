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

interface AppStore {
  isConnected: boolean;
  walletMonitor: WalletMonitorState;
  liveSignals: LiveSignal[];
  confidenceGaugeScore: number | null;
  confidenceThreshold: number;
  confidenceHistory: ConfidenceHistoryPoint[];
  tradeLog: TradeLogEntry[];
  metrics: MetricsState;
  
  // Actions
  setConnected: (connected: boolean) => void;
  updateWalletMonitor: (data: Partial<WalletMonitorState>) => void;
  addSignal: (signal: LiveSignal) => void;
  setConfidenceGaugeScore: (score: number | null) => void;
  setConfidenceHistory: (points: ConfidenceHistoryPoint[]) => void;
  addTrade: (trade: TradeLogEntry) => void;
  updateMetrics: (metrics: Partial<MetricsState>) => void;
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
  confidenceGaugeScore: null, // Shows --% when null
  confidenceThreshold: 75,
  confidenceHistory: [], // Shows skeleton chart when empty
  tradeLog: [], // Shows empty list layout when empty
  metrics: {
    winRate: "--",
    triggersToday: "--",
    alertsFiredCount: "--",
    alertsFiredTotal: "--"
  },

  setConnected: (connected) => set({ isConnected: connected }),
  
  updateWalletMonitor: (data) => set((state) => ({
    walletMonitor: { ...state.walletMonitor, ...data }
  })),
  
  addSignal: (signal) => set((state) => ({
    liveSignals: [signal, ...state.liveSignals].slice(0, 50)
  })),
  
  setConfidenceGaugeScore: (score) => set({ confidenceGaugeScore: score }),
  
  setConfidenceHistory: (points) => set({ confidenceHistory: points }),
  
  addTrade: (trade) => set((state) => ({
    tradeLog: [trade, ...state.tradeLog].slice(0, 50)
  })),
  
  updateMetrics: (newMetrics) => set((state) => ({
    metrics: { ...state.metrics, ...newMetrics }
  }))
}))
