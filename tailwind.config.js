/** @type {import('tailwindcss').Config} */
// The theme below was previously declared inline in shuffify/templates/base.html
// as `tailwind.config = {...}` for the Play CDN. It lives here now so the
// standalone CLI compiles the same utilities the templates already use.
// A class referenced by a template but missing from this theme silently
// produces no CSS, so keep the two in step.
module.exports = {
  content: [
    "./shuffify/templates/**/*.html",
    "./shuffify/static/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        'spotify-green': '#1DB954',
        'spotify-dark': '#191414',
        'dark-base': '#0a0a0f',
        'dark-surface': '#0f0f17',
        'dark-card': '#141420',
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
  plugins: [],
}
