/** @type {import('tailwindcss').Config} */
// After Hours uses one design package: ./assets/design builds the theme from
// tokens.json and supplies the component plugin.
const design = require('./assets/design');
module.exports = {
  content: [
    "./assets/design/templates/**/*.j2",
    "./assets/legal/**/*.html",
    // Static legal documents share the canonical design recipes.
    "./shuffify/static/public/**/*.html",
  ],
  theme: {
    extend: {
      ...design.theme,
      colors: {
        'spotify-green': '#1DB954',
        'spotify-dark': '#191414',
        'dark-base': '#0a0a0f',
        'dark-surface': '#0f0f17',
        'dark-card': '#141420',
        ...design.theme.colors,
      },
      animation: {
        'fade-in': 'fadeIn 0.6s ease-out',
        'slide-up': 'slideUp 0.6s ease-out',
        'scale-in': 'scaleIn 0.4s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' }
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(40px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' }
        },
        scaleIn: {
          '0%': { opacity: '0', transform: 'scale(0.9)' },
          '100%': { opacity: '1', transform: 'scale(1)' }
        }
      }
    },
  },
  plugins: [design.plugin],
}
