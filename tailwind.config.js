/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './static/**/*.js',
  ],
  safelist: [
    // ── Dynamically built class strings in JS ──────────────────────────────
    // Backgrounds
    'bg-amber-50',
    'bg-blue-50', 'bg-blue-100', 'bg-blue-600', 'bg-blue-800',
    'bg-cyan-100',
    'bg-emerald-50',
    'bg-gray-50', 'bg-gray-200', 'bg-gray-300', 'bg-gray-400',
    'bg-gray-500', 'bg-gray-700', 'bg-gray-800', 'bg-gray-950',
    'bg-green-50', 'bg-green-100', 'bg-green-200', 'bg-green-300',
    'bg-green-400', 'bg-green-500', 'bg-green-600', 'bg-green-700',
    'bg-green-800', 'bg-green-950',
    'bg-indigo-950',
    'bg-orange-100',
    'bg-red-50', 'bg-red-100', 'bg-red-200', 'bg-red-300',
    'bg-red-400', 'bg-red-500', 'bg-red-600', 'bg-red-800',
    'bg-red-900', 'bg-red-950',
    'bg-yellow-50', 'bg-yellow-500', 'bg-yellow-600', 'bg-yellow-700',
    // Text
    'text-blue-300', 'text-blue-400', 'text-blue-500', 'text-blue-600',
    'text-blue-700', 'text-blue-800',
    'text-cyan-400',
    'text-gray-300', 'text-gray-400', 'text-gray-500', 'text-gray-600',
    'text-gray-700', 'text-gray-800', 'text-gray-900',
    'text-green-200', 'text-green-300', 'text-green-400', 'text-green-500',
    'text-green-600', 'text-green-700', 'text-green-800', 'text-green-900',
    'text-indigo-400', 'text-indigo-800',
    'text-orange-300', 'text-orange-400', 'text-orange-500',
    'text-orange-600', 'text-orange-700', 'text-orange-800',
    'text-pink-400',
    'text-purple-600', 'text-purple-700', 'text-purple-800',
    'text-red-200', 'text-red-300', 'text-red-400', 'text-red-500',
    'text-red-600', 'text-red-700', 'text-red-800', 'text-red-900',
    'text-teal-300',
    'text-yellow-300', 'text-yellow-400', 'text-yellow-500',
    'text-yellow-600', 'text-yellow-700', 'text-yellow-800', 'text-yellow-900',
    // Borders
    'border-blue-400', 'border-blue-500', 'border-blue-700',
    'border-gray-100', 'border-gray-200', 'border-gray-300',
    'border-gray-400', 'border-gray-500', 'border-gray-600',
    'border-gray-700', 'border-gray-800', 'border-gray-900',
    'border-green-200', 'border-green-300', 'border-green-400',
    'border-green-500', 'border-green-600', 'border-green-700', 'border-green-900',
    'border-indigo-500',
    'border-l-4', 'border-t-2', 'border-b-2',
    'border-orange-500', 'border-orange-700',
    'border-purple-500',
    'border-red-200', 'border-red-300', 'border-red-400',
    'border-red-500', 'border-red-600', 'border-red-900',
    'border-teal-700',
    'border-yellow-200', 'border-yellow-400', 'border-yellow-500',
    'border-yellow-600', 'border-yellow-800', 'border-yellow-900',
    // Gradients
    'from-green-500', 'to-blue-400', 'to-emerald-400',
    // Ring
    'ring-2', 'ring-blue-200', 'ring-indigo-400',
    'ring-offset-0', 'ring-yellow-400',
    // Misc
    'opacity-50',
    'shadow-sm', 'shadow-md', 'shadow-lg',
  ],
  theme: {
    extend: {
      colors: {
        // Walmart brand colours
        blue:  { 100: '#0053e2', 110: '#0047c7', 130: '#003db3' },
        spark: { 100: '#ffc220', 140: '#995213' },
      },
      animation: {
        'pulse-glow': 'pulse-glow 2s ease-in-out infinite',
        'live-pulse': 'live-pulse 2s ease-in-out infinite',
        'slide-in':   'slide-in 0.3s ease-out',
      },
      keyframes: {
        'pulse-glow': {
          '0%, 100%': { boxShadow: '0 0 10px rgba(0,83,226,0.3)' },
          '50%':      { boxShadow: '0 0 25px rgba(0,83,226,0.6)' },
        },
        'live-pulse': {
          '0%, 100%': { opacity: 1 },
          '50%':      { opacity: 0.4 },
        },
        'slide-in': {
          from: { opacity: 0, transform: 'translateY(20px)' },
          to:   { opacity: 1, transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
};
