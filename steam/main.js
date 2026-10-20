// Межа — the Steam edition's window: Electron opens the game (index.html, built by build.py) and passes its
// achievements on to Steam through steamworks.js. Without Steam running the game still works, with no achievements.
const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const pkg = require('./package.json');

const noOverlay = process.argv.includes('--no-overlay');
let steam = null;
try {
  const sw = require('steamworks.js');
  // started outside Steam (double-click on Mezha.exe) the game restarts through Steam, as Steam asks of every game;
  // a steam_appid.txt next to the program (only for testing) turns that off
  if (!require('fs').existsSync(path.join(path.dirname(process.execPath), 'steam_appid.txt')) && sw.restartAppIfNecessary(pkg.steamAppId)) { app.exit(0); }
  steam = sw.init(pkg.steamAppId);
  if (!noOverlay) sw.electronEnableSteamOverlay();
} catch (e) {
  console.error('Steam is not available:', e && e.message);
}

app.setName('Mezha');
function createWindow() {
  const win = new BrowserWindow({
    width: 1600, height: 900, minWidth: 960, minHeight: 600, show: false,
    title: 'Межа', backgroundColor: '#0a0d11', autoHideMenuBar: true,
    icon: path.join(__dirname, 'icon.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true, spellcheck: false }
  });
  win.removeMenu();
  win.once('ready-to-show', () => { win.maximize(); win.show(); });
  // F11 — full screen, Alt+Enter as well (as in most games)
  win.webContents.on('before-input-event', (e, inp) => {
    if (inp.type !== 'keyDown') return;
    if (inp.key === 'F11' || (inp.key === 'Enter' && inp.alt)) { win.setFullScreen(!win.isFullScreen()); e.preventDefault(); }
  });
  // links open in the system browser; the game window never leaves the game
  win.webContents.setWindowOpenHandler(({ url }) => { if (/^https?:/.test(url)) shell.openExternal(url); return { action: 'deny' }; });
  win.webContents.on('will-navigate', (e, url) => { if (!url.startsWith('file:')) { e.preventDefault(); if (/^https?:/.test(url)) shell.openExternal(url); } });
  win.loadFile(path.join(__dirname, 'index.html'));
}

ipcMain.on('mezha:achieve', (_, id) => {
  if (!steam || typeof id !== 'string' || !/^ACH_[A-Z_]+$/.test(id)) return;
  try { if (!steam.achievement.isActivated(id)) steam.achievement.activate(id); } catch (e) { console.error('achievement', id, e && e.message); }
});
ipcMain.on('mezha:quit', () => app.quit());
ipcMain.handle('mezha:user', () => { try { return steam ? steam.localplayer.getName() : ''; } catch (e) { return ''; } });

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());
