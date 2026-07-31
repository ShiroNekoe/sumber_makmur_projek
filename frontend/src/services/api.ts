export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8002/api/v1';
export const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8002/ws';

export const endpoints = {
  status: `${API_BASE_URL}/status`,
  retrain: `${API_BASE_URL}/retrain`,
  ws: WS_BASE_URL,
  dashboardStats: `${API_BASE_URL}/dashboard/stats`,
  dashboardSignals: `${API_BASE_URL}/dashboard/signals`,
  dashboardTrades: `${API_BASE_URL}/dashboard/trades`,
  dashboardPositions: `${API_BASE_URL}/dashboard/positions`,
  dashboardStatus: `${API_BASE_URL}/dashboard/status`,
  walletCandidates: `${API_BASE_URL}/dashboard/wallets/candidates`,
  approveWallet: (addr: string) => `${API_BASE_URL}/dashboard/wallets/${addr}/approve`,
  dashboardErrors: `${API_BASE_URL}/dashboard/errors`,
  dashboardPortfolio: `${API_BASE_URL}/dashboard/portfolio`,
  dashboardActiveWallets: `${API_BASE_URL}/dashboard/wallets/active`,
  dashboardExportPdf: `${API_BASE_URL}/dashboard/export/pdf`,
  dashboardWallets: `${API_BASE_URL}/dashboard/wallets`,
};

export const exportPortfolioPdfUrl = (startDate?: string, endDate?: string) => {
  let url = endpoints.dashboardExportPdf;
  const params = [];
  if (startDate) params.push(`start_date=${encodeURIComponent(startDate)}`);
  if (endDate) params.push(`end_date=${encodeURIComponent(endDate)}`);
  if (params.length > 0) {
    url += `?${params.join('&')}`;
  }
  return url;
};

export const fetchDashboardPortfolio = async () => {
  const res = await fetch(endpoints.dashboardPortfolio);
  if (!res.ok) throw new Error('Failed to fetch portfolio summary');
  return res.json();
};

export const fetchDashboardStats = async () => {
  const res = await fetch(endpoints.dashboardStats);
  if (!res.ok) throw new Error('Failed to fetch dashboard stats');
  return res.json();
};

export const fetchRecentSignals = async (hours = 24) => {
  const res = await fetch(`${endpoints.dashboardSignals}?hours=${hours}`);
  if (!res.ok) throw new Error('Failed to fetch signals');
  return res.json();
};

export const fetchRecentTrades = async (limit = 30) => {
  const res = await fetch(`${endpoints.dashboardTrades}?limit=${limit}`);
  if (!res.ok) throw new Error('Failed to fetch closed trades');
  return res.json();
};

export const fetchOpenPositions = async () => {
  const res = await fetch(endpoints.dashboardPositions);
  if (!res.ok) throw new Error('Failed to fetch open positions');
  return res.json();
};

export const fetchSystemStatus = async () => {
  const res = await fetch(endpoints.dashboardStatus);
  if (!res.ok) throw new Error('Failed to fetch system status');
  return res.json();
};

export const fetchWalletCandidates = async () => {
  const res = await fetch(endpoints.walletCandidates);
  if (!res.ok) throw new Error('Failed to fetch wallet candidates');
  return res.json();
};

export const approveWallet = async (address: string, action: 'approve' | 'reject') => {
  const res = await fetch(endpoints.approveWallet(address), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
  if (!res.ok) throw new Error(`Failed to ${action} wallet`);
  return res.json();
};

export const triggerManualRetrain = async () => {
  const res = await fetch(endpoints.retrain, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to trigger manual retrain');
  return res.json();
};

export const fetchSystemErrors = async (limit = 50) => {
  const res = await fetch(`${endpoints.dashboardErrors}?limit=${limit}`);
  if (!res.ok) throw new Error('Failed to fetch system error logs');
  return res.json();
};

export const fetchActiveWallets = async () => {
  const res = await fetch(endpoints.dashboardActiveWallets);
  if (!res.ok) throw new Error('Failed to fetch active wallets');
  return res.json();
};

export const addManualWallet = async (walletAddress: string, label?: string) => {
  const res = await fetch(endpoints.dashboardWallets, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ wallet_address: walletAddress, label }),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail || 'Failed to add wallet to watchlist');
  }
  return res.json();
};

export const deleteManualWallet = async (walletAddress: string) => {
  const res = await fetch(`${endpoints.dashboardWallets}/${walletAddress}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail || 'Failed to remove wallet from watchlist');
  }
  return res.json();
};
