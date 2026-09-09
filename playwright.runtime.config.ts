import { defineConfig, devices } from "@playwright/test";


const baseURL = process.env["FORTWEB_BASE_URL"];
if (!baseURL) {
    throw new Error("FORTWEB_BASE_URL is required");
}
const outputDir = process.env["FORTWEB_PLAYWRIGHT_OUTPUT_DIR"];
if (!outputDir) {
    throw new Error("FORTWEB_PLAYWRIGHT_OUTPUT_DIR is required");
}

export default defineConfig({
    testDir: "./playwright",
    outputDir,
    fullyParallel: false,
    workers: 1,
    retries: 0,
    reporter: "list",
    use: {
        baseURL,
        serviceWorkers: "block",
        trace: "off",
    },
    projects: [
        {
            name: "chromium",
            use: { ...devices["Desktop Chrome"] },
        },
    ],
});
