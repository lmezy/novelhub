/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{vue,js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        paper: "#f6f4ed",
        ink: "#202924",
        muted: "#626d66",
        accent: "#236454",
        surface: "#fffefa",
        border: "#dedfd5",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["Noto Serif SC", "Georgia", "serif"],
      },
    },
  },
  plugins: [],
}
