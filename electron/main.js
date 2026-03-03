const { app, BrowserWindow, dialog, Menu } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const http = require("http");

const APP_ROOT = path.resolve(__dirname, "..");
const PY_ENTRY = path.join(APP_ROOT, "app.py");
const HOST = "127.0.0.1";
const PORT = 7860;
const START_TIMEOUT_MS = 120000;

let pyProc = null;
let mainWindow = null;

function waitForServer(url, timeoutMs) {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(url, (res) => {
        res.resume();
        resolve();
      });

      req.on("error", () => {
        if (Date.now() - startedAt > timeoutMs) {
          reject(new Error("Timed out waiting for Python server startup."));
          return;
        }
        setTimeout(tick, 1000);
      });

      req.setTimeout(2000, () => {
        req.destroy();
      });
    };
    tick();
  });
}

function startPythonServer() {
  const pythonCmd = process.platform === "win32" ? "python" : "python3";
  const args = [PY_ENTRY, "--host", HOST, "--port", String(PORT), "--no-browser"];

  pyProc = spawn(pythonCmd, args, {
    cwd: APP_ROOT,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"]
  });

  pyProc.stdout.on("data", (data) => {
    process.stdout.write(`[python] ${data}`);
  });

  pyProc.stderr.on("data", (data) => {
    process.stderr.write(`[python] ${data}`);
  });

  pyProc.on("exit", (code, signal) => {
    const msg = `Python process exited (code=${code}, signal=${signal}).`;
    process.stderr.write(`${msg}\n`);
    if (!app.isQuitting) {
      dialog.showErrorBox("Backend exited", msg);
      app.quit();
    }
  });
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 720,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  mainWindow.setMenuBarVisibility(false);

  const url = `http://${HOST}:${PORT}`;
  await waitForServer(url, START_TIMEOUT_MS);
  await mainWindow.loadURL(url);
}

function stopPythonServer() {
  if (!pyProc) return;
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(pyProc.pid), "/f", "/t"]);
  } else {
    pyProc.kill("SIGTERM");
  }
  pyProc = null;
}

app.on("before-quit", () => {
  app.isQuitting = true;
  stopPythonServer();
});

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  startPythonServer();
  try {
    await createWindow();
  } catch (err) {
    dialog.showErrorBox("Startup error", String(err));
    app.quit();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
