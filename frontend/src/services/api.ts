export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
export const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000/ws';

export const endpoints = {
  status: `${API_BASE_URL}/status`,
  retrain: `${API_BASE_URL}/retrain`,
  ws: WS_BASE_URL,
};
