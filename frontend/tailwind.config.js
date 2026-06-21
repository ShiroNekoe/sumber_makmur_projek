/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        cyber: {
          bg: "#080B11",
          card: "#0E131F",
          cardLight: "#161E30",
          border: "#1F2B48",
          textMuted: "#6B7C96",
          accent: "#4F46E5", // Indigo
          emerald: "#10B981", // Active / Profit
          rose: "#F43F5E", // Loss / SL
          amber: "#F59E0B"  // Hold / Warning
        }
      },
      animation: {
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'glow-pulse': 'glow 2s ease-in-out infinite'
      },
      keyframes: {
        glow: {
          '0%, 100%': { opacity: '0.4' },
          '50%': { opacity: '1' }
        }
      }
    },
  },
  plugins: [],
}
