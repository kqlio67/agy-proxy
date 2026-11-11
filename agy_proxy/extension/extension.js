const vscode = require('vscode');
const http = require('http');
const url = require('url');

let statusBarItem;
let pollTimer;

function getProxyUrl() {
  const config = vscode.workspace.getConfiguration('agyProxy');
  return config.get('url', 'http://127.0.0.1:8000').replace(/\/+$/, '');
}

function fetchJson(endpoint, options = {}) {
  return new Promise((resolve, reject) => {
    const fullUrl = `${getProxyUrl()}${endpoint}`;
    const parsed = url.parse(fullUrl);
    const reqOpts = {
      hostname: parsed.hostname,
      port: parsed.port || 80,
      path: parsed.path,
      method: options.method || 'GET',
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        ...(options.headers || {})
      },
      timeout: 3000
    };

    const req = http.request(reqOpts, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const parsedData = JSON.parse(data);
          resolve({ status: res.statusCode, data: parsedData });
        } catch (e) {
          resolve({ status: res.statusCode, data: data });
        }
      });
    });

    req.on('error', err => reject(err));
    req.on('timeout', () => {
      req.destroy();
      reject(new Error('Connection timed out'));
    });

    if (options.body) {
      req.write(typeof options.body === 'string' ? options.body : JSON.stringify(options.body));
    }
    req.end();
  });
}

async function updateStatusBar() {
  if (!statusBarItem) return;

  try {
    const [accRes, routRes] = await Promise.all([
      fetchJson('/api/accounts').catch(() => null),
      fetchJson('/api/providers/routing').catch(() => null),
    ]);

    if (!accRes || accRes.status !== 200 || !accRes.data) {
      statusBarItem.text = `$(circle-slash) AGY: Offline`;
      statusBarItem.tooltip = `Antigravity Proxy is offline at ${getProxyUrl()}.\nClick to open options.`;
      statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
      statusBarItem.show();
      return;
    }

    const accounts = accRes.data.accounts || [];
    const routing = routRes ? (routRes.data || {}) : {};
    const failoverActive = Boolean(routing.auto_failover_enabled || routing.enable_external_failover);

    let sumGemini = 0;
    let sumClaude = 0;
    let count = 0;

    accounts.forEach(a => {
      if (a.enabled !== false && a.auth_method !== 'api_key') {
        const q = a.quota_details || {};
        const g = q.gemini ? Math.round(q.gemini.percent ?? (q.gemini.fraction * 100)) : 100;
        const c = (q['3p'] || q.claude) ? Math.round((q['3p'] || q.claude).percent ?? ((q['3p'] || q.claude).fraction * 100)) : 100;
        sumGemini += g;
        sumClaude += c;
        count++;
      }
    });

    const avgGemini = count > 0 ? Math.round(sumGemini / count) : 100;
    const avgClaude = count > 0 ? Math.round(sumClaude / count) : 100;

    let icon = '$(sparkle)';
    if (avgClaude <= 10 && avgGemini <= 10) icon = '$(alert)';

    const failoverBadge = failoverActive ? ' 🔄' : '';
    statusBarItem.text = `${icon} AGY: ${avgClaude}% 3.7 | ${avgGemini}% 2.5${failoverBadge}`;
    statusBarItem.backgroundColor = undefined;

    // Detailed multi-line tooltip
    let tooltipLines = [
      `### Antigravity Proxy Hub (${getProxyUrl()})`,
      `**Pool Accounts:** ${accounts.length} (${count} active OAuth)`,
      `**Claude 3.7/Sonnet Quota:** ${avgClaude}%`,
      `**Gemini 2.5 Pro Quota:** ${avgGemini}%`,
      `**429 Auto-Failover:** ${failoverActive ? `Active (${routing.fallback_model || 'default'})` : 'Off'}`,
      '---',
      'Click for Quick Actions (Clean Cache, Switch Account, Web Hub)'
    ];
    statusBarItem.tooltip = new vscode.MarkdownString(tooltipLines.join('\n\n'));
    statusBarItem.show();
  } catch (err) {
    statusBarItem.text = `$(circle-slash) AGY: Err`;
    statusBarItem.tooltip = `Error connecting to proxy: ${err.message}`;
    statusBarItem.show();
  }
}

async function showQuickMenu() {
  const items = [
    {
      label: '$(globe) Open Web Dashboard',
      description: `${getProxyUrl()}`,
      action: 'dashboard'
    },
    {
      label: '$(trash) Clean IDE & Logs Cache',
      description: 'Purge crash dumps, Electron GPU caches, and temporary logs (preserves Brain artifacts)',
      action: 'clean_cache'
    },
    {
      label: '$(arrow-swap) Switch Active Account',
      description: 'Select primary Google or external model account',
      action: 'switch_account'
    },
    {
      label: '$(shuffle) Toggle 429 Auto-Failover',
      description: 'Enable/disable fallback to Ollama / OpenRouter when Google quotas hit 429',
      action: 'toggle_failover'
    },
    {
      label: '$(refresh) Refresh Quota & Status',
      description: 'Poll proxy immediately for quota updates',
      action: 'refresh'
    }
  ];

  const selected = await vscode.window.showQuickPick(items, {
    placeHolder: 'Antigravity Proxy Actions'
  });

  if (!selected) return;

  switch (selected.action) {
    case 'dashboard':
      vscode.env.openExternal(vscode.Uri.parse(getProxyUrl()));
      break;

    case 'clean_cache':
      await runCleanCache();
      break;

    case 'switch_account':
      await runSwitchAccount();
      break;

    case 'toggle_failover':
      await runToggleFailover();
      break;

    case 'refresh':
      await updateStatusBar();
      vscode.window.showInformationMessage('Antigravity Proxy quota status refreshed.');
      break;
  }
}

async function runCleanCache() {
  try {
    vscode.window.withProgress({
      location: vscode.ProgressLocation.Notification,
      title: 'Cleaning Antigravity IDE & Logs Cache...',
      cancellable: false
    }, async () => {
      const res = await fetchJson('/api/cache/clean', { method: 'POST', body: {} });
      if (res.status === 200 && res.data) {
        const freed = res.data.freed_formatted || '0 B';
        const files = res.data.deleted_files || 0;
        vscode.window.showInformationMessage(`🧹 IDE Cache Cleaned! Freed ${freed} across ${files} files.`);
      } else {
        vscode.window.showWarningMessage(`Cache clean finished with status ${res.status}`);
      }
    });
  } catch (err) {
    vscode.window.showErrorMessage(`Failed to clean cache: ${err.message}`);
  }
}

async function runSwitchAccount() {
  try {
    const res = await fetchJson('/api/accounts');
    if (!res || !res.data || !res.data.accounts) {
      vscode.window.showErrorMessage('Unable to retrieve accounts from agy-proxy.');
      return;
    }

    const accounts = res.data.accounts;
    const accountItems = accounts.map(a => ({
      label: `$(account) ${a.name || a.email || a.account_id}`,
      description: `${a.auth_method} | Gemini: ${a.quota_details?.gemini?.percent ?? 100}% | Claude: ${a.quota_details?.['3p']?.percent ?? 100}%`,
      accountId: a.account_id
    }));

    const picked = await vscode.window.showQuickPick(accountItems, {
      placeHolder: 'Select account to activate as primary'
    });

    if (picked) {
      const actRes = await fetchJson(`/api/accounts/${encodeURIComponent(picked.accountId)}/activate`, {
        method: 'POST',
        body: { target: 'all' }
      });
      if (actRes.status === 200) {
        vscode.window.showInformationMessage(`Active session switched to: ${picked.label}`);
        await updateStatusBar();
      } else {
        vscode.window.showWarningMessage(`Switch response: ${JSON.stringify(actRes.data)}`);
      }
    }
  } catch (err) {
    vscode.window.showErrorMessage(`Failed to switch account: ${err.message}`);
  }
}

async function runToggleFailover() {
  try {
    const routRes = await fetchJson('/api/providers/routing');
    const currentSettings = routRes.data || {};
    const currentlyEnabled = Boolean(currentSettings.auto_failover_enabled || currentSettings.enable_external_failover);
    const newState = !currentlyEnabled;

    await fetchJson('/api/providers/routing', {
      method: 'POST',
      body: {
        enable_external_failover: newState,
        fallback_model: currentSettings.fallback_model || null
      }
    });

    vscode.window.showInformationMessage(`429 Auto-Failover is now ${newState ? 'ENABLED' : 'DISABLED'}.`);
    await updateStatusBar();
  } catch (err) {
    vscode.window.showErrorMessage(`Failed to toggle failover: ${err.message}`);
  }
}

function activate(context) {
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.command = 'agy-proxy.showMenu';
  context.subscriptions.push(statusBarItem);

  context.subscriptions.push(vscode.commands.registerCommand('agy-proxy.showMenu', showQuickMenu));
  context.subscriptions.push(vscode.commands.registerCommand('agy-proxy.showDashboard', () => vscode.env.openExternal(vscode.Uri.parse(getProxyUrl()))));
  context.subscriptions.push(vscode.commands.registerCommand('agy-proxy.cleanCache', runCleanCache));
  context.subscriptions.push(vscode.commands.registerCommand('agy-proxy.switchAccount', runSwitchAccount));
  context.subscriptions.push(vscode.commands.registerCommand('agy-proxy.toggleFailover', runToggleFailover));
  context.subscriptions.push(vscode.commands.registerCommand('agy-proxy.refreshQuota', updateStatusBar));

  const config = vscode.workspace.getConfiguration('agyProxy');
  const intervalSeconds = config.get('refreshIntervalSeconds', 30);

  updateStatusBar();
  pollTimer = setInterval(updateStatusBar, intervalSeconds * 1000);
}

function deactivate() {
  if (pollTimer) clearInterval(pollTimer);
  if (statusBarItem) statusBarItem.dispose();
}

module.exports = {
  activate,
  deactivate
};
