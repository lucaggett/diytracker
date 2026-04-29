module.exports = {
  content: [
    './templates/**/*.html',
    './templates/_partials/**/*.html',
    './templates/legal/**/*.html',
    './templates/errors/**/*.html',
    './static/js/**/*.js'
  ],
  safelist: [
    'bg-paper',
    'bg-ink',
    'text-paper',
    'text-ink',
    'text-bleed',
    'border-ink',
    'border-bleed',
    { pattern: /^(bg|text|border)-(paper|ink|bleed|bleed-offset|smudge)$/ },
  ],
  theme: {
    extend: {
      colors: {
        paper: '#ece7dc',
        ink: '#0a0a0a',
        bleed: '#c21a1a',
        'bleed-offset': '#1a4ec2',
        smudge: '#6b6259',
      },
      fontFamily: {
        richEatin: ['RichEatinAllCaps', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
        serif: ['"Times New Roman"', 'Times', 'Georgia', 'serif'],
      },
      backgroundImage: {
        'halftone-sm': 'radial-gradient(circle, var(--ink) 0.8px, transparent 1.2px)',
        'halftone-md': 'radial-gradient(circle, var(--ink) 1px, transparent 1.6px)',
        'halftone-lg': 'radial-gradient(circle, var(--ink) 1.4px, transparent 2.2px)',
      },
      backgroundSize: {
        'halftone-sm': '3px 3px',
        'halftone-md': '4px 4px',
        'halftone-lg': '6px 6px',
      },
      keyframes: {
        jitter: {
          '0%,100%': { transform: 'translate(0,0) rotate(0deg)' },
          '25%': { transform: 'translate(-0.5px,0.5px) rotate(-0.2deg)' },
          '50%': { transform: 'translate(0.5px,-0.5px) rotate(0.2deg)' },
          '75%': { transform: 'translate(-0.5px,-0.5px) rotate(-0.1deg)' },
        },
        slam: {
          '0%': { transform: 'rotate(-18deg) scale(2.4)', opacity: '0' },
          '60%': { transform: 'rotate(6deg) scale(0.92)', opacity: '1' },
          '100%': { transform: 'rotate(-8deg) scale(1)', opacity: '1' },
        },
        drift: {
          '0%,100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-2px)' },
        },
        misreg: {
          '0%,100%': { textShadow: '2px 0 0 var(--bleed), -2px 0 0 var(--bleed-offset)' },
          '50%': { textShadow: '3px 1px 0 var(--bleed), -3px -1px 0 var(--bleed-offset)' },
        },
        pop: {
          '0%': { transform: 'scale(1)' },
          '40%': { transform: 'scale(0.92)' },
          '100%': { transform: 'scale(1)' },
        },
      },
      animation: {
        jitter: 'jitter 3.2s ease-in-out infinite',
        slam: 'slam 600ms cubic-bezier(0.22, 1, 0.36, 1) 1',
        drift: 'drift 6s ease-in-out infinite',
        misreg: 'misreg 4s ease-in-out infinite',
        pop: 'pop 220ms cubic-bezier(0.22, 1, 0.36, 1) 1',
      },
    }
  },
  plugins: [],
}
