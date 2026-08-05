import {
  StrictMode,
} from "react";
import {
  createRoot,
} from "react-dom/client";

import App from "./App";
import {
  AuthProvider,
} from "./providers/AuthProvider";
import {
  ThemeProvider,
} from "./providers/ThemeProvider";

import "./styles/design-tokens.css";
import "./index.css";

const rootElement =
  document.getElementById("root");

if (!rootElement) {
  throw new Error(
    "Elemento raiz #root não encontrado.",
  );
}

createRoot(rootElement).render(
  <StrictMode>
    <ThemeProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </ThemeProvider>
  </StrictMode>,
);
