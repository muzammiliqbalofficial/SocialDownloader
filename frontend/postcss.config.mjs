/** Tailwind 4 ships its own PostCSS plugin; autoprefixer is no longer needed. */
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
