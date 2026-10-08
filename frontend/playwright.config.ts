import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const data = mkdtempSync(join(tmpdir(), "sentinel-e2e-"));
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    viewport: { width: 1440, height: 1120 },
    ...(process.env.CHROMIUM_PATH
      ? {
          launchOptions: {
            executablePath: process.env.CHROMIUM_PATH,
            args: [
              "--no-sandbox",
              "--disable-dev-shm-usage",
              "--disable-gpu",
              "--disable-software-rasterizer",
              "--no-zygote",
              "--single-process",
            ],
          },
        }
      : {}),
  },
  webServer: {
    command: "python ../scripts/dev.py",
    url: "http://127.0.0.1:5173",
    timeout: 30000,
    env: {
      DATABASE_URL: `sqlite:///${data}/control.db`,
      SANDBOX_DB: join(data, "sandbox.db"),
    },
  },
});
