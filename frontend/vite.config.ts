import {
  defineConfig,
  loadEnv,
  type ProxyOptions,
} from "vite";
import react from "@vitejs/plugin-react";

function createApiProxy(
  backendUrl: string,
  apiKey: string,
): ProxyOptions {
  return {
    target: backendUrl,
    changeOrigin: true,
    secure: false,
    headers: apiKey
      ? {
          "X-API-Key": apiKey,
        }
      : undefined,
    rewrite: (path) => {
      const rewritten = path.replace(
        /^\/api(?=\/|$)/,
        "",
      );

      return rewritten || "/";
    },
  };
}

export default defineConfig(
  ({ command, mode }) => {
    const env = loadEnv(
      mode,
      process.cwd(),
      "",
    );

    const backendUrl = (
      process.env.AUNERON_BACKEND_URL ||
      env.AUNERON_BACKEND_URL ||
      "http://127.0.0.1:8000"
    ).trim();

    const apiKey = (
      process.env.AUNERON_API_KEY ||
      env.AUNERON_API_KEY ||
      ""
    ).trim();

    if (
      command === "serve" &&
      !apiKey
    ) {
      console.warn(
        "[Auneron] AUNERON_API_KEY não está definida. " +
          "As rotas protegidas retornarão HTTP 401.",
      );
    }

    const apiProxy = createApiProxy(
      backendUrl,
      apiKey,
    );

    return {
      plugins: [
        react(),
      ],
      server: {
        proxy: {
          "/api": apiProxy,
        },
      },
      preview: {
        proxy: {
          "/api": apiProxy,
        },
      },
    };
  },
);
