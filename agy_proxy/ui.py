"""
Web Dashboard and Chat Playground HTML Template for Antigravity Proxy.
Includes Multi-Account Pool Manager, Live Quota Gauges, and Interactive Playground.
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Google Antigravity Proxy</title>
  <link rel="icon" type="image/x-icon" href="/favicon.ico">
  <link rel="icon" type="image/png" href="/assets/image/antigravity-logo.png">
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: {
              50: '#eef2ff',
              500: '#6366f1',
              600: '#4f46e5',
              700: '#4338ca',
            },
            dark: {
              bg: '#0f172a',
              card: '#1e293b',
              cardHover: '#334155',
              border: '#334155',
            }
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #0f172a; color: #f1f5f9; font-family: ui-sans-serif, system-ui, sans-serif; }
    .custom-scrollbar::-webkit-scrollbar { width: 6px; height: 6px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: #1e293b; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: #475569; border-radius: 3px; }
    .thought-bubble { border-left: 3px solid #6366f1; background: rgba(99, 102, 241, 0.08); }
    .prose pre { background: #0b1120; border-radius: 0.5rem; padding: 0.75rem; overflow-x: auto; }
    .prose code { color: #38bdf8; }
  </style>
</head>
<body class="min-h-screen flex flex-col custom-scrollbar">

  <!-- Header -->
  <header class="border-b border-dark-border bg-slate-900/80 backdrop-blur sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <a href="/" class="flex items-center space-x-3 group">
          <div class="w-10 h-10 rounded-xl bg-slate-800 border border-slate-700/60 p-1 flex items-center justify-center shadow-lg group-hover:border-indigo-500/50 transition">
            <img src="/assets/image/antigravity-logo.png" alt="Google Antigravity" class="w-full h-full object-contain">
          </div>
          <div>
            <div class="flex items-center space-x-2">
              <span class="text-lg font-black tracking-tight text-white">Google <span class="text-transparent bg-clip-text bg-gradient-to-r from-blue-400 via-indigo-400 to-purple-400">Antigravity</span> Proxy</span>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 font-semibold border border-blue-500/20">Pool Active</span>
            </div>
            <p class="text-[11px] text-slate-400 font-mono">OpenAI · Anthropic · Gemini Multi-Account Gateway</p>
          </div>
        </a>
      </div>

      <!-- Quick Nav / Links -->
      <div class="flex items-center space-x-3">
        <!-- Update Available Badge (dynamic) -->
        <div id="updateBadgeContainer" class="hidden">
          <button onclick="openUpdateModal()" class="flex items-center space-x-1.5 text-xs font-semibold px-2.5 py-1.5 rounded-lg bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 hover:bg-indigo-500/30 transition animate-pulse">
            <i class="fa-solid fa-sparkles text-amber-400"></i>
            <span id="updateBadgeText">Update v1.0.3</span>
          </button>
        </div>

        <button onclick="openAddAccountModal()" class="flex items-center space-x-2 text-xs font-semibold px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white transition shadow-sm">
          <i class="fa-solid fa-user-plus"></i>
          <span>Add Account</span>
        </button>
        <button onclick="refreshAllAccounts()" id="refreshBtn" class="flex items-center space-x-2 text-xs font-semibold px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 transition">
          <i class="fa-solid fa-rotate" id="refreshIcon"></i>
          <span>Refresh All</span>
        </button>
        <a href="#playground" class="text-xs font-semibold px-3 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition shadow-sm">
          <i class="fa-solid fa-terminal mr-1"></i> Playground
        </a>
      </div>
    </div>
  </header>

  <!-- Main Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">

    <!-- Global Update Banner (Shown when new release is detected) -->
    <div id="updateBanner" class="hidden p-4 rounded-2xl bg-gradient-to-r from-indigo-900/60 via-purple-900/40 to-slate-900 border border-indigo-500/40 text-xs shadow-lg flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <div class="flex items-center space-x-3">
        <div class="w-8 h-8 rounded-xl bg-indigo-600/30 border border-indigo-400/30 flex items-center justify-center text-amber-300 text-sm flex-shrink-0">
          <i class="fa-solid fa-gift"></i>
        </div>
        <div>
          <div class="font-bold text-white text-sm flex items-center space-x-2">
            <span>New Version Available: <span id="bannerNewVer" class="text-emerald-400">v1.0.3</span></span>
            <span class="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/30 text-indigo-200 border border-indigo-500/40">Latest</span>
          </div>
          <p class="text-slate-300 text-[11px] mt-0.5" id="bannerReleaseName">A new release is available on GitHub.</p>
        </div>
      </div>
      <div class="flex items-center space-x-2 flex-shrink-0">
        <button onclick="openUpdateModal()" class="px-3 py-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold transition flex items-center space-x-1.5 shadow">
          <i class="fa-solid fa-circle-info"></i>
          <span>View Release / Update</span>
        </button>
        <button onclick="dismissUpdateBanner()" class="text-slate-400 hover:text-white p-1.5" title="Dismiss banner">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
    </div>

    <!-- Multi-Account Pool Section -->
    <div class="space-y-4">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div class="flex items-center space-x-2">
          <i class="fa-solid fa-users text-indigo-400"></i>
          <h2 class="text-base font-bold text-white">Active Account Pool</h2>
          <span class="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700" id="accountPoolCount">0 accounts</span>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <!-- View Switcher (Grid vs Table) -->
          <div class="flex items-center bg-slate-900 border border-slate-800 rounded-lg p-0.5 text-xs">
            <button id="viewBtnGrid" onclick="setAccountView('grid')" class="px-2.5 py-1 rounded-md font-medium text-white bg-indigo-600 transition flex items-center space-x-1" title="Grid Card View">
              <i class="fa-solid fa-grip"></i>
              <span class="hidden sm:inline">Grid</span>
            </button>
            <button id="viewBtnTable" onclick="setAccountView('table')" class="px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-white transition flex items-center space-x-1" title="Compact Table View">
              <i class="fa-solid fa-list"></i>
              <span class="hidden sm:inline">Compact</span>
            </button>
          </div>

          <button onclick="toggleAllAccounts(true)" class="text-[11px] px-2.5 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800 font-medium transition flex items-center space-x-1" title="Enable all accounts">
            <i class="fa-solid fa-play text-emerald-400 text-[10px]"></i>
            <span>Enable All</span>
          </button>
          <button onclick="toggleAllAccounts(false)" class="text-[11px] px-2.5 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800 font-medium transition flex items-center space-x-1" title="Pause / Disable all accounts">
            <i class="fa-solid fa-pause text-amber-400 text-[10px]"></i>
            <span>Disable All</span>
          </button>
        </div>
      </div>

      <!-- Account Filter & Search Toolbar -->
      <div class="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2.5 bg-slate-900/60 p-2.5 rounded-xl border border-slate-800/80 text-xs">
        <!-- Search Input -->
        <div class="relative flex-1 max-w-md">
          <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-slate-500"></i>
          <input
            type="text"
            id="accountSearchInput"
            placeholder="Filter accounts by name, email, or key..."
            class="w-full bg-slate-900 border border-slate-700/80 rounded-lg pl-8 pr-3 py-1.5 text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition text-xs"
            oninput="renderAccounts()"
          >
        </div>

        <!-- Filter Tabs & Sort Dropdown -->
        <div class="flex flex-wrap items-center gap-2">
          <div class="flex items-center space-x-1 bg-slate-900 rounded-lg p-0.5 border border-slate-800" id="accountTypeFilters">
            <button onclick="setAccountFilter('all')" data-filter="all" class="acc-filter-btn px-2 py-1 rounded text-[11px] font-medium bg-indigo-600 text-white">All (<span id="countAll">0</span>)</button>
            <button onclick="setAccountFilter('oauth')" data-filter="oauth" class="acc-filter-btn px-2 py-1 rounded text-[11px] font-medium text-slate-400 hover:text-white">OAuth (<span id="countOAuth">0</span>)</button>
            <button onclick="setAccountFilter('apikey')" data-filter="apikey" class="acc-filter-btn px-2 py-1 rounded text-[11px] font-medium text-slate-400 hover:text-white">API Keys (<span id="countApiKey">0</span>)</button>
            <button onclick="setAccountFilter('exhausted')" data-filter="exhausted" class="acc-filter-btn px-2 py-1 rounded text-[11px] font-medium text-slate-400 hover:text-red-400">0% (<span id="countExhausted">0</span>)</button>
          </div>

          <select id="accountSortSelect" onchange="renderAccounts()" class="bg-slate-900 border border-slate-700/80 rounded-lg px-2.5 py-1.5 text-slate-300 text-[11px] focus:outline-none focus:border-indigo-500">
            <option value="default">Sort: Default (Primary first)</option>
            <option value="quota-desc">Sort: Highest Quota</option>
            <option value="quota-asc">Sort: Lowest Quota</option>
            <option value="reqs-desc">Sort: Most Requests</option>
            <option value="name-asc">Sort: Name (A-Z)</option>
          </select>
        </div>
      </div>

      <!-- Accounts Container (Grid or Compact Table) -->
      <div id="accountsContainer">
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" id="accountsGrid">
          <div class="p-5 rounded-2xl bg-dark-card border border-dark-border text-center text-slate-500 text-sm col-span-full">
            Loading accounts...
          </div>
        </div>
      </div>
    </div>

    <!-- Playground & Models Layout -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-8" id="playground">

      <!-- Left Column: Controls & Models -->
      <div class="lg:col-span-1 space-y-6">

        <!-- Config Panel -->
        <div class="p-6 rounded-2xl bg-dark-card border border-dark-border space-y-4">
          <h2 class="text-base font-bold text-white flex items-center">
            <i class="fa-solid fa-sliders mr-2 text-indigo-400"></i> Playground Settings
          </h2>

          <div class="space-y-3 text-sm">
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1">Model</label>
              <select id="modelSelect" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500 transition">
                <option value="gemini-3.7-flash-high">Gemini 3.7 Flash (High Reasoning)</option>
                <option value="gemini-pro-agent">Gemini Pro Agent (3.1 Pro)</option>
                <option value="gemini-3.1-flash-lite">Gemini 3.1 Flash Lite</option>
                <option value="claude-sonnet-4-6">Claude Sonnet 4.6 (Thinking)</option>
                <option value="claude-opus-4-6-thinking">Claude Opus 4.6 (Thinking)</option>
                <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                <option value="gpt-oss-120b-medium">GPT-OSS 120B</option>
              </select>
            </div>

            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1">System Prompt</label>
              <textarea id="systemPrompt" rows="2" placeholder="You are a helpful coding assistant..." class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500 custom-scrollbar transition"></textarea>
            </div>

            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1">Temperature (<span id="tempVal">0.7</span>)</label>
                <input type="range" id="tempSlider" min="0" max="2" step="0.1" value="0.7" class="w-full accent-indigo-500" oninput="document.getElementById('tempVal').innerText=this.value">
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-400 mb-1">Max Output Tokens</label>
                <input type="number" id="maxTokens" value="2048" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500">
              </div>
            </div>

            <div class="pt-2 flex items-center justify-between">
              <label class="text-xs font-medium text-slate-300 flex items-center cursor-pointer">
                <input type="checkbox" id="streamToggle" checked class="mr-2 accent-indigo-500 rounded">
                Stream Response (SSE)
              </label>
              <button onclick="clearChat()" class="text-xs text-slate-400 hover:text-red-400 transition">
                <i class="fa-solid fa-trash-can mr-1"></i> Clear Chat
              </button>
            </div>
          </div>
        </div>

        <!-- Available Models Catalog -->
        <div class="p-6 rounded-2xl bg-dark-card border border-dark-border space-y-3.5">
          <div class="flex items-center justify-between">
            <h2 class="text-base font-bold text-white flex items-center">
              <i class="fa-solid fa-cubes mr-2 text-purple-400"></i> Model Catalog & Quota
            </h2>
            <span class="text-xs font-mono text-slate-400" id="modelCount">0 models</span>
          </div>

          <!-- Real-time Model Search Input -->
          <div class="relative">
            <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-xs text-slate-500"></i>
            <input 
              type="text" 
              id="modelSearchInput" 
              placeholder="Search 50+ models (e.g. flash, claude, 3.7)..." 
              class="w-full bg-slate-900 border border-slate-700/80 rounded-xl pl-8 pr-3 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition"
              oninput="filterModelCatalog()"
            >
          </div>

          <div class="space-y-2.5 max-h-80 overflow-y-auto pr-1 custom-scrollbar" id="modelList">
            <div class="text-center py-6 text-slate-500 text-sm">Loading model catalog...</div>
          </div>
        </div>

      </div>

      <!-- Right Column: Interactive Chat Interface -->
      <div class="lg:col-span-2 flex flex-col h-[720px] rounded-2xl bg-dark-card border border-dark-border overflow-hidden">

        <!-- Chat Header -->
        <div class="p-4 border-b border-dark-border bg-slate-900/60 flex items-center justify-between">
          <div class="flex items-center space-x-2">
            <i class="fa-solid fa-comments text-indigo-400"></i>
            <span class="font-bold text-sm text-white">Live Playground</span>
            <span class="text-xs font-mono text-indigo-300 bg-indigo-500/10 px-2 py-0.5 rounded border border-indigo-500/20" id="activeModelBadge">gemini-3.7-flash-high</span>
          </div>
          <div class="text-xs text-slate-400" id="tokenUsageBadge"></div>
        </div>

        <!-- Chat Messages Container -->
        <div class="flex-1 p-6 overflow-y-auto space-y-6 custom-scrollbar" id="chatContainer">
          <div class="text-center py-16 text-slate-500">
            <div class="w-12 h-12 mx-auto rounded-full bg-slate-800 flex items-center justify-center text-slate-400 mb-3">
              <i class="fa-solid fa-wand-magic-sparkles text-xl"></i>
            </div>
            <p class="text-sm font-medium">Type a message below to start chatting with Antigravity backend.</p>
            <p class="text-xs text-slate-600 mt-1">Multi-account routing and thought extraction are handled automatically.</p>
          </div>
        </div>

        <!-- Chat Input Form -->
        <div class="p-4 border-t border-dark-border bg-slate-900/90">
          <form onsubmit="sendMessage(event)" class="flex space-x-3">
            <textarea
              id="userInput"
              rows="2"
              placeholder="Ask anything or request code..."
              class="flex-1 bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 custom-scrollbar resize-none transition"
              onkeydown="if(event.key === 'Enter' && !event.shiftKey){ event.preventDefault(); sendMessage(event); }"
            ></textarea>
            <button
              type="submit"
              id="sendBtn"
              class="px-5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-medium flex items-center justify-center transition shadow-lg shadow-indigo-600/30 disabled:opacity-50"
            >
              <i class="fa-solid fa-paper-plane text-base" id="sendIcon"></i>
            </button>
          </form>
        </div>

      </div>

    </div>

    <!-- Quick Integrations Guide -->
    <div class="p-6 rounded-2xl bg-dark-card border border-dark-border space-y-5">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
        <div>
          <h2 class="text-base font-bold text-white flex items-center">
            <i class="fa-solid fa-plug-circle-bolt mr-2 text-emerald-400"></i> Client Integrations & Setup Guides
          </h2>
          <p class="text-xs text-slate-400 mt-0.5">Click any snippet or copy button to instantly copy ready-to-use configurations.</p>
        </div>
        <div class="flex items-center space-x-2">
          <span class="text-xs font-mono text-slate-400">Endpoint:</span>
          <div class="flex items-center bg-slate-900 border border-slate-700/80 rounded-lg px-2.5 py-1 space-x-2">
            <code id="endpointUrlText" class="text-xs font-mono text-indigo-400">http://localhost:8000/v1</code>
            <button onclick="copySnippet('endpointUrlText', this)" class="text-slate-400 hover:text-white transition" title="Copy base URL">
              <i class="fa-regular fa-copy text-xs"></i>
            </button>
          </div>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4.5 text-xs">

        <!-- 1. Claude Code CLI -->
        <div class="p-4 rounded-xl bg-slate-900 border border-slate-800/90 space-y-3 flex flex-col justify-between hover:border-indigo-500/40 transition group">
          <div class="space-y-1.5">
            <div class="font-bold text-slate-200 text-sm flex items-center justify-between">
              <span class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded-lg bg-indigo-600/20 text-indigo-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-code"></i>
                </div>
                <span>Claude Code CLI</span>
              </span>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 font-semibold">Anthropic</span>
            </div>
            <p class="text-[11px] text-slate-400 leading-snug">Terminal coding agent with full Tool Calling & Thinking.</p>
          </div>

          <div class="relative bg-slate-950 rounded-xl border border-slate-800 p-2.5">
            <button onclick="copySnippet('codeClaudeCode', this)" class="absolute top-2 right-2 px-2 py-1 rounded-md bg-slate-800/80 hover:bg-indigo-600 text-slate-300 hover:text-white text-[10px] transition flex items-center space-x-1" title="Copy code">
              <i class="fa-regular fa-copy"></i>
              <span>Copy</span>
            </button>
            <pre id="codeClaudeCode" class="text-slate-300 font-mono text-[11px] overflow-x-auto custom-scrollbar pt-1 pr-12"># 1-Click Fast Launcher
./run_claude.sh

# Or via Env Variables:
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_API_KEY=dummy
claude</pre>
          </div>
        </div>

        <!-- 2. Cursor / Windsurf -->
        <div class="p-4 rounded-xl bg-slate-900 border border-slate-800/90 space-y-3 flex flex-col justify-between hover:border-cyan-500/40 transition group">
          <div class="space-y-1.5">
            <div class="font-bold text-slate-200 text-sm flex items-center justify-between">
              <span class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded-lg bg-cyan-600/20 text-cyan-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-arrow-pointer"></i>
                </div>
                <span>Cursor / Windsurf</span>
              </span>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 font-semibold">OpenAI</span>
            </div>
            <p class="text-[11px] text-slate-400 leading-snug">AI code editors with Custom OpenAI provider support.</p>
          </div>

          <div class="relative bg-slate-950 rounded-xl border border-slate-800 p-2.5">
            <button onclick="copySnippet('codeCursor', this)" class="absolute top-2 right-2 px-2 py-1 rounded-md bg-slate-800/80 hover:bg-cyan-600 text-slate-300 hover:text-white text-[10px] transition flex items-center space-x-1" title="Copy code">
              <i class="fa-regular fa-copy"></i>
              <span>Copy</span>
            </button>
            <pre id="codeCursor" class="text-slate-300 font-mono text-[11px] overflow-x-auto custom-scrollbar pt-1 pr-12">Base URL: http://localhost:8000/v1
API Key:  dummy
Model:    gemini-3.7-flash-high</pre>
          </div>
        </div>

        <!-- 3. Roo Code / Cline (VS Code) -->
        <div class="p-4 rounded-xl bg-slate-900 border border-slate-800/90 space-y-3 flex flex-col justify-between hover:border-purple-500/40 transition group">
          <div class="space-y-1.5">
            <div class="font-bold text-slate-200 text-sm flex items-center justify-between">
              <span class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded-lg bg-purple-600/20 text-purple-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-robot"></i>
                </div>
                <span>Roo Code / Cline</span>
              </span>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-400 border border-purple-500/20 font-semibold">VS Code</span>
            </div>
            <p class="text-[11px] text-slate-400 leading-snug">Autonomous coding assistant extensions for VS Code.</p>
          </div>

          <div class="relative bg-slate-950 rounded-xl border border-slate-800 p-2.5">
            <button onclick="copySnippet('codeRooCode', this)" class="absolute top-2 right-2 px-2 py-1 rounded-md bg-slate-800/80 hover:bg-purple-600 text-slate-300 hover:text-white text-[10px] transition flex items-center space-x-1" title="Copy code">
              <i class="fa-regular fa-copy"></i>
              <span>Copy</span>
            </button>
            <pre id="codeRooCode" class="text-slate-300 font-mono text-[11px] overflow-x-auto custom-scrollbar pt-1 pr-12">Provider: OpenAI Compatible / Anthropic
Base URL: http://localhost:8000/v1
API Key:  dummy
Model:    gemini-3.7-flash-high</pre>
          </div>
        </div>

        <!-- 4. Continue.dev -->
        <div class="p-4 rounded-xl bg-slate-900 border border-slate-800/90 space-y-3 flex flex-col justify-between hover:border-emerald-500/40 transition group">
          <div class="space-y-1.5">
            <div class="font-bold text-slate-200 text-sm flex items-center justify-between">
              <span class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded-lg bg-emerald-600/20 text-emerald-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-forward"></i>
                </div>
                <span>Continue.dev</span>
              </span>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-semibold">JetBrains / VSCode</span>
            </div>
            <p class="text-[11px] text-slate-400 leading-snug">Open-source AI autocomplete and chat copilot.</p>
          </div>

          <div class="relative bg-slate-950 rounded-xl border border-slate-800 p-2.5">
            <button onclick="copySnippet('codeContinue', this)" class="absolute top-2 right-2 px-2 py-1 rounded-md bg-slate-800/80 hover:bg-emerald-600 text-slate-300 hover:text-white text-[10px] transition flex items-center space-x-1" title="Copy code">
              <i class="fa-regular fa-copy"></i>
              <span>Copy</span>
            </button>
            <pre id="codeContinue" class="text-slate-300 font-mono text-[10px] overflow-x-auto custom-scrollbar pt-1 pr-12">{
  "models": [{
    "title": "Antigravity Gemini",
    "provider": "openai",
    "model": "gemini-3.7-flash-high",
    "apiBase": "http://localhost:8000/v1",
    "apiKey": "dummy"
  }]
}</pre>
          </div>
        </div>

        <!-- 5. Aider CLI -->
        <div class="p-4 rounded-xl bg-slate-900 border border-slate-800/90 space-y-3 flex flex-col justify-between hover:border-amber-500/40 transition group">
          <div class="space-y-1.5">
            <div class="font-bold text-slate-200 text-sm flex items-center justify-between">
              <span class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded-lg bg-amber-600/20 text-amber-400 flex items-center justify-center text-xs">
                  <i class="fa-solid fa-keyboard"></i>
                </div>
                <span>Aider CLI</span>
              </span>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 font-semibold">Pair-Coder</span>
            </div>
            <p class="text-[11px] text-slate-400 leading-snug">Terminal pair-programming assistant with git integration.</p>
          </div>

          <div class="relative bg-slate-950 rounded-xl border border-slate-800 p-2.5">
            <button onclick="copySnippet('codeAider', this)" class="absolute top-2 right-2 px-2 py-1 rounded-md bg-slate-800/80 hover:bg-amber-600 text-slate-300 hover:text-white text-[10px] transition flex items-center space-x-1" title="Copy code">
              <i class="fa-regular fa-copy"></i>
              <span>Copy</span>
            </button>
            <pre id="codeAider" class="text-slate-300 font-mono text-[11px] overflow-x-auto custom-scrollbar pt-1 pr-12">OPENAI_API_BASE="http://localhost:8000/v1" \
OPENAI_API_KEY="dummy" \
aider --model openai/gemini-3.7-flash-high</pre>
          </div>
        </div>

        <!-- 6. Python SDK -->
        <div class="p-4 rounded-xl bg-slate-900 border border-slate-800/90 space-y-3 flex flex-col justify-between hover:border-yellow-500/40 transition group">
          <div class="space-y-1.5">
            <div class="font-bold text-slate-200 text-sm flex items-center justify-between">
              <span class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded-lg bg-yellow-600/20 text-yellow-400 flex items-center justify-center text-xs">
                  <i class="fa-brands fa-python"></i>
                </div>
                <span>Python SDK</span>
              </span>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-yellow-500/10 text-yellow-400 border border-yellow-500/20 font-semibold">OpenAI / Anthropic</span>
            </div>
            <p class="text-[11px] text-slate-400 leading-snug">Scripting and building apps with official SDK clients.</p>
          </div>

          <div class="relative bg-slate-950 rounded-xl border border-slate-800 p-2.5">
            <button onclick="copySnippet('codePython', this)" class="absolute top-2 right-2 px-2 py-1 rounded-md bg-slate-800/80 hover:bg-yellow-600 text-slate-300 hover:text-white text-[10px] transition flex items-center space-x-1" title="Copy code">
              <i class="fa-regular fa-copy"></i>
              <span>Copy</span>
            </button>
            <pre id="codePython" class="text-slate-300 font-mono text-[10.5px] overflow-x-auto custom-scrollbar pt-1 pr-12">from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dummy"
)
res = client.chat.completions.create(
    model="gemini-3.7-flash-high",
    messages=[{"role": "user", "content": "Hi!"}]
)</pre>
          </div>
        </div>

        <!-- 7. Gemini Native API (v1beta) -->
        <div class="p-4 rounded-xl bg-slate-900 border border-slate-800/90 space-y-3 flex flex-col justify-between hover:border-blue-500/40 transition group">
          <div class="space-y-1.5">
            <div class="font-bold text-slate-200 text-sm flex items-center justify-between">
              <span class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded-lg bg-blue-600/20 text-blue-400 flex items-center justify-center text-xs">
                  <i class="fa-brands fa-google"></i>
                </div>
                <span>Gemini Native API</span>
              </span>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20 font-semibold">v1beta Native</span>
            </div>
            <p class="text-[11px] text-slate-400 leading-snug">Raw Google Generative Language REST API format.</p>
          </div>

          <div class="relative bg-slate-950 rounded-xl border border-slate-800 p-2.5">
            <button onclick="copySnippet('codeGeminiNative', this)" class="absolute top-2 right-2 px-2 py-1 rounded-md bg-slate-800/80 hover:bg-blue-600 text-slate-300 hover:text-white text-[10px] transition flex items-center space-x-1" title="Copy code">
              <i class="fa-regular fa-copy"></i>
              <span>Copy</span>
            </button>
            <pre id="codeGeminiNative" class="text-slate-300 font-mono text-[10px] overflow-x-auto custom-scrollbar pt-1 pr-12">curl http://localhost:8000/v1beta/models/gemini-3.7-flash-high:streamGenerateContent \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"role":"user","parts":[{"text":"Hello!"}]}]}'</pre>
          </div>
        </div>

        <!-- 8. Remote Cloudflare Tunnel -->
        <div class="p-4 rounded-xl bg-slate-900 border border-slate-800/90 space-y-3 flex flex-col justify-between hover:border-orange-500/40 transition group">
          <div class="space-y-1.5">
            <div class="font-bold text-slate-200 text-sm flex items-center justify-between">
              <span class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded-lg bg-orange-600/20 text-orange-400 flex items-center justify-center text-xs">
                  <i class="fa-brands fa-cloudflare"></i>
                </div>
                <span>Remote / Tunnel</span>
              </span>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-orange-500/10 text-orange-400 border border-orange-500/20 font-semibold">Edge Access</span>
            </div>
            <p class="text-[11px] text-slate-400 leading-snug">Public HTTPS URL without port forwarding.</p>
          </div>

          <div class="relative bg-slate-950 rounded-xl border border-slate-800 p-2.5">
            <button onclick="copySnippet('codeCloudflare', this)" class="absolute top-2 right-2 px-2 py-1 rounded-md bg-slate-800/80 hover:bg-orange-600 text-slate-300 hover:text-white text-[10px] transition flex items-center space-x-1" title="Copy code">
              <i class="fa-regular fa-copy"></i>
              <span>Copy</span>
            </button>
            <pre id="codeCloudflare" class="text-slate-300 font-mono text-[10px] overflow-x-auto custom-scrollbar pt-1 pr-12"># 1. Start Instant Tunnel
cloudflared tunnel --url http://localhost:8000

# 2. Use generated remote URL:
https://your-tunnel.trycloudflare.com/v1</pre>
          </div>
        </div>

      </div>
    </div>

  </main>

  <!-- Add Account Modal -->
  <div id="addAccountModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
    <div class="bg-dark-card border border-dark-border rounded-2xl max-w-lg w-full p-6 space-y-5 shadow-2xl">
      <div class="flex items-center justify-between border-b border-dark-border pb-4">
        <h3 class="text-lg font-bold text-white flex items-center">
          <i class="fa-solid fa-user-plus mr-2 text-indigo-400"></i> Add Account / Provider to Pool
        </h3>
        <button onclick="closeAddAccountModal()" class="text-slate-400 hover:text-white text-lg">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <!-- Tab Switcher -->
      <div class="flex p-1 bg-slate-900 rounded-xl border border-slate-800 text-xs">
        <button id="tabBtnOAuth" onclick="switchAddTab('oauth')" class="flex-1 py-2 rounded-lg font-semibold transition bg-indigo-600 text-white shadow">
          <i class="fa-brands fa-google mr-1.5"></i> Google Antigravity (OAuth)
        </button>
        <button id="tabBtnApiKey" onclick="switchAddTab('apikey')" class="flex-1 py-2 rounded-lg font-semibold transition text-slate-400 hover:text-white">
          <i class="fa-solid fa-key mr-1.5"></i> Gemini API Key (AI Studio)
        </button>
      </div>

      <!-- Tab 1: Google OAuth Content -->
      <div id="tabContentOAuth" class="space-y-4 text-xs">
        <div class="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
          <div class="font-semibold text-slate-200 text-sm flex items-center">
            <span class="w-5 h-5 rounded-full bg-indigo-600 text-white flex items-center justify-center mr-2 text-xs">1</span>
            Authenticate in Browser
          </div>
          <p class="text-slate-400">Click the button below to sign in with your secondary Google account:</p>
          <a id="oauthLoginLink" href="#" target="_blank" class="inline-flex items-center space-x-2 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-medium transition shadow">
            <i class="fa-solid fa-arrow-up-right-from-square"></i>
            <span>Open Google OAuth Sign-In</span>
          </a>
        </div>

        <div class="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
          <div class="font-semibold text-slate-200 text-sm flex items-center">
            <span class="w-5 h-5 rounded-full bg-indigo-600 text-white flex items-center justify-center mr-2 text-xs">2</span>
            Paste Authorization Code or Redirect URL
          </div>
          <p class="text-slate-400">After authorizing, paste the code (or entire callback URL) below:</p>
          <textarea id="authCodeInput" rows="2" placeholder="4/0ATsMZ... or https://antigravity.google/oauth-callback?code=..." class="w-full bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 text-white font-mono focus:outline-none focus:border-indigo-500 custom-scrollbar"></textarea>
        </div>
      </div>

      <!-- Tab 2: Gemini API Key Content -->
      <div id="tabContentApiKey" class="space-y-4 text-xs hidden">
        <div class="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
          <div class="font-semibold text-slate-200 text-sm flex items-center">
            <i class="fa-solid fa-key mr-2 text-amber-400"></i> Google AI Studio API Key
          </div>
          <p class="text-slate-400">Add a direct Gemini API key from <a href="https://aistudio.google.com/app/apikey" target="_blank" class="text-indigo-400 underline">Google AI Studio</a>:</p>
          <div class="space-y-2.5 pt-1">
            <div>
              <label class="text-[11px] text-slate-400 block mb-1 font-medium">Account Display Name</label>
              <input type="text" id="apiKeyLabelInput" placeholder="e.g. Work Gemini API Key" class="w-full bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 text-white text-xs focus:outline-none focus:border-indigo-500">
            </div>
            <div>
              <label class="text-[11px] text-slate-400 block mb-1 font-medium">Gemini API Key</label>
              <input type="password" id="apiKeyValueInput" placeholder="AIzaSy..." class="w-full bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 text-white font-mono text-xs focus:outline-none focus:border-indigo-500">
            </div>
          </div>
        </div>
      </div>

      <div class="flex items-center justify-end space-x-3 pt-2">
        <button onclick="closeAddAccountModal()" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition">
          Cancel
        </button>
        <button onclick="submitAddAccountModal()" id="submitAuthBtn" class="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold flex items-center space-x-2 transition shadow">
          <i class="fa-solid fa-check"></i>
          <span>Add to Pool</span>
        </button>
      </div>
    </div>
  </div>

  <!-- Rename Account Modal -->
  <div id="renameModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
    <div class="bg-dark-card border border-dark-border rounded-2xl max-w-sm w-full p-5 space-y-4 shadow-2xl">
      <div class="flex items-center justify-between border-b border-dark-border pb-3">
        <h3 class="text-sm font-bold text-white flex items-center">
          <i class="fa-solid fa-pen-to-square mr-2 text-indigo-400"></i> Rename Account / Key
        </h3>
        <button onclick="closeRenameModal()" class="text-slate-400 hover:text-white text-base">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
      <div>
        <label class="text-[11px] text-slate-400 block mb-1 font-medium">Display Name</label>
        <input type="text" id="renameInput" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white text-xs focus:outline-none focus:border-indigo-500 transition" placeholder="e.g. Personal Gemini Key" onkeydown="if(event.key==='Enter') submitRenameModal(); if(event.key==='Escape') closeRenameModal();">
      </div>
      <div class="flex items-center justify-end space-x-2.5 pt-1">
        <button onclick="closeRenameModal()" class="px-3.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition">
          Cancel
        </button>
        <button onclick="submitRenameModal()" class="px-4 py-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold transition shadow">
          Save
        </button>
      </div>
    </div>
  </div>

  <!-- Update Modal -->
  <div id="updateModal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
    <div class="bg-dark-card border border-dark-border rounded-2xl max-w-lg w-full p-6 space-y-5 shadow-2xl">
      <div class="flex items-center justify-between border-b border-dark-border pb-4">
        <div class="flex items-center space-x-2.5">
          <div class="w-8 h-8 rounded-xl bg-indigo-600/30 border border-indigo-400/30 flex items-center justify-center text-amber-400 text-sm">
            <i class="fa-solid fa-sparkles"></i>
          </div>
          <div>
            <h3 class="text-base font-bold text-white">Software Update Available</h3>
            <p class="text-xs text-slate-400" id="updateModalSub">v1.0.2 $\rightarrow$ v1.0.3</p>
          </div>
        </div>
        <button onclick="closeUpdateModal()" class="text-slate-400 hover:text-white text-lg">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <div class="space-y-3 text-xs">
        <div class="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
          <div class="font-semibold text-slate-200 flex items-center justify-between">
            <span id="modalReleaseTitle">Release Highlights</span>
            <a id="modalGitHubLink" href="#" target="_blank" class="text-indigo-400 hover:underline flex items-center space-x-1 font-mono text-[11px]">
              <span>GitHub Release</span>
              <i class="fa-solid fa-arrow-up-right-from-square text-[9px]"></i>
            </a>
          </div>
          <div id="modalReleaseBody" class="text-slate-300 max-h-48 overflow-y-auto whitespace-pre-wrap font-mono text-[11px] bg-slate-950 p-2.5 rounded-lg border border-slate-800/80 custom-scrollbar">Loading release notes...</div>
        </div>

        <div id="updateActionGit" class="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-2.5 hidden">
          <div class="font-semibold text-slate-200 flex items-center">
            <i class="fa-solid fa-wand-magic-sparkles text-emerald-400 mr-2"></i> 1-Click Update (Git Pull)
          </div>
          <p class="text-slate-400 text-[11px]">Pull latest commits directly into this local directory:</p>
          <div class="flex items-center space-x-2">
            <button onclick="executeGitPull()" id="btnGitPull" class="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold flex items-center space-x-2 transition shadow">
              <i class="fa-solid fa-download"></i>
              <span>Pull & Update Now</span>
            </button>
          </div>
          <div id="gitPullOutput" class="hidden p-2 rounded-lg bg-slate-950 font-mono text-[10px] text-slate-300 whitespace-pre-wrap border border-slate-800"></div>
        </div>

        <div id="updateActionManual" class="p-3.5 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
          <div class="font-semibold text-slate-200 flex items-center">
            <i class="fa-solid fa-terminal text-cyan-400 mr-2"></i> Manual Command
          </div>
          <pre class="bg-slate-950 p-2.5 rounded-lg border border-slate-800/80 font-mono text-[11px] text-slate-300">git pull origin main</pre>
        </div>
      </div>

      <div class="flex items-center justify-end space-x-2.5 pt-1 border-t border-slate-800">
        <button onclick="closeUpdateModal()" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition">
          Close
        </button>
      </div>
    </div>
  </div>

  <!-- Script -->
  <script>
    let messagesHistory = [];
    let isGenerating = false;
    let currentOAuthState = null;
    let currentOAuthVerifier = null;
    let rawAccountsData = [];
    let currentAccountView = 'grid';
    let currentAccountFilter = 'all';

    function setAccountView(mode) {
      currentAccountView = mode;
      const btnGrid = document.getElementById('viewBtnGrid');
      const btnTable = document.getElementById('viewBtnTable');
      if (mode === 'grid') {
        btnGrid.className = 'px-2.5 py-1 rounded-md font-medium text-white bg-indigo-600 transition flex items-center space-x-1';
        btnTable.className = 'px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-white transition flex items-center space-x-1';
      } else {
        btnTable.className = 'px-2.5 py-1 rounded-md font-medium text-white bg-indigo-600 transition flex items-center space-x-1';
        btnGrid.className = 'px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-white transition flex items-center space-x-1';
      }
      renderAccounts();
    }

    function setAccountFilter(filter) {
      currentAccountFilter = filter;
      document.querySelectorAll('.acc-filter-btn').forEach(btn => {
        if (btn.dataset.filter === filter) {
          btn.className = 'acc-filter-btn px-2 py-1 rounded text-[11px] font-medium bg-indigo-600 text-white';
        } else {
          btn.className = 'acc-filter-btn px-2 py-1 rounded text-[11px] font-medium text-slate-400 hover:text-white';
        }
      });
      renderAccounts();
    }

    function formatResetTime(isoStr) {
      if (!isoStr) return '';
      try {
        const resetDate = new Date(isoStr);
        const now = new Date();
        const diffMs = resetDate - now;
        if (diffMs <= 0) return 'Ready';

        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMins / 60);
        const diffDays = Math.floor(diffHours / 24);

        const timeStr = resetDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        if (diffDays > 0) {
          const remHours = diffHours % 24;
          return remHours > 0 ? `in ${diffDays}d ${remHours}h` : `in ${diffDays}d`;
        } else if (diffHours > 0) {
          const remMins = diffMins % 60;
          return remMins > 0 ? `in ${diffHours}h ${remMins}m` : `in ${diffHours}h`;
        } else {
          return `in ${Math.max(1, diffMins)}m`;
        }
      } catch (e) {
        return '';
      }
    }

    document.getElementById('modelSelect').addEventListener('change', (e) => {
      document.getElementById('activeModelBadge').innerText = e.target.value;
    });

    async function loadAccounts() {
      try {
        const res = await fetch('/api/accounts');
        const data = await res.json();
        rawAccountsData = data.accounts || [];
        renderAccounts();
      } catch (err) {
        console.error("Accounts load failed", err);
      }
    }

    function renderAccounts() {
      const container = document.getElementById('accountsContainer');
      const searchQ = (document.getElementById('accountSearchInput')?.value || '').toLowerCase().trim();
      const sortMode = document.getElementById('accountSortSelect')?.value || 'default';

      // Calculate counts for filters
      let totalAll = rawAccountsData.length;
      let totalOAuth = 0;
      let totalApiKey = 0;
      let totalExhausted = 0;

      rawAccountsData.forEach(acc => {
        const isApiKey = acc.auth_method === 'api_key';
        if (isApiKey) totalApiKey++;
        else totalOAuth++;

        const qDetails = acc.quota_details || {};
        const geminiQ = qDetails.gemini || { percent: 100, fraction: 1.0 };
        const claudeQ = qDetails['3p'] || qDetails.claude || { percent: 100, fraction: 1.0 };
        const geminiPct = Math.round(geminiQ.percent ?? (geminiQ.fraction * 100));
        const claudePct = Math.round(claudeQ.percent ?? (claudeQ.fraction * 100));
        if (!isApiKey && (geminiPct <= 0 || claudePct <= 0)) {
          totalExhausted++;
        }
      });

      if (document.getElementById('countAll')) document.getElementById('countAll').innerText = totalAll;
      if (document.getElementById('countOAuth')) document.getElementById('countOAuth').innerText = totalOAuth;
      if (document.getElementById('countApiKey')) document.getElementById('countApiKey').innerText = totalApiKey;
      if (document.getElementById('countExhausted')) document.getElementById('countExhausted').innerText = totalExhausted;

      document.getElementById('accountPoolCount').innerText = `${totalAll} account${totalAll === 1 ? '' : 's'}`;

      if (totalAll === 0) {
        container.innerHTML = '<div class="p-8 rounded-2xl bg-dark-card border border-dark-border text-center text-slate-500 text-sm">No accounts found. Click "Add Account" to connect one.</div>';
        return;
      }

      // Filter
      let filtered = rawAccountsData.filter(acc => {
        const isApiKey = acc.auth_method === 'api_key';
        if (currentAccountFilter === 'oauth' && isApiKey) return false;
        if (currentAccountFilter === 'apikey' && !isApiKey) return false;

        const qDetails = acc.quota_details || {};
        const geminiQ = qDetails.gemini || { percent: 100, fraction: 1.0 };
        const claudeQ = qDetails['3p'] || qDetails.claude || { percent: 100, fraction: 1.0 };
        const geminiPct = Math.round(geminiQ.percent ?? (geminiQ.fraction * 100));
        const claudePct = Math.round(claudeQ.percent ?? (claudeQ.fraction * 100));

        if (currentAccountFilter === 'exhausted') {
          if (isApiKey) return false;
          if (geminiPct > 0 && claudePct > 0) return false;
        }

        if (searchQ) {
          const name = (acc.name || '').toLowerCase();
          const email = (acc.email || '').toLowerCase();
          const proj = (acc.project_id || '').toLowerCase();
          if (!name.includes(searchQ) && !email.includes(searchQ) && !proj.includes(searchQ)) {
            return false;
          }
        }
        return true;
      });

      // Sort
      filtered.sort((a, b) => {
        if (sortMode === 'quota-desc' || sortMode === 'quota-asc') {
          const getAvgQuota = (acc) => {
            if (acc.auth_method === 'api_key') return 100;
            const q = acc.quota_details || {};
            const g = q.gemini ? Math.round(q.gemini.percent ?? (q.gemini.fraction * 100)) : 100;
            const c = (q['3p'] || q.claude) ? Math.round((q['3p']||q.claude).percent ?? ((q['3p']||q.claude).fraction * 100)) : 100;
            return (g + c) / 2;
          };
          const diff = getAvgQuota(b) - getAvgQuota(a);
          return sortMode === 'quota-desc' ? diff : -diff;
        }
        if (sortMode === 'reqs-desc') {
          return (b.total_requests || 0) - (a.total_requests || 0);
        }
        if (sortMode === 'name-asc') {
          return (a.name || a.email || '').localeCompare(b.name || b.email || '');
        }
        // Default: Primary first, then original order
        if (a.is_primary && !b.is_primary) return -1;
        if (!a.is_primary && b.is_primary) return 1;
        return 0;
      });

      if (filtered.length === 0) {
        container.innerHTML = `<div class="p-8 rounded-2xl bg-dark-card border border-dark-border text-center text-slate-500 text-sm">No accounts match the selected filter/search.</div>`;
        return;
      }

      if (currentAccountView === 'grid') {
        // --- GRID VIEW ---
        let html = '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" id="accountsGrid">';
        filtered.forEach(acc => {
          const isEnabled = acc.enabled !== false;
          const isApiKey = acc.auth_method === 'api_key';
          const qDetails = acc.quota_details || {};
          const geminiQ = qDetails.gemini || { percent: 100, fraction: 1.0 };
          const claudeQ = qDetails['3p'] || qDetails.claude || { percent: 100, fraction: 1.0 };
          const geminiPct = Math.round(geminiQ.percent ?? (geminiQ.fraction * 100));
          const claudePct = Math.round(claudeQ.percent ?? (claudeQ.fraction * 100));
          const geminiReset = formatResetTime(geminiQ.reset_time);
          const claudeReset = formatResetTime(claudeQ.reset_time);
          const geminiIsExhausted = geminiPct <= 0 || (acc.rate_limited_models && acc.rate_limited_models.gemini !== undefined);
          const claudeIsExhausted = claudePct <= 0 || (acc.rate_limited_models && acc.rate_limited_models['3p'] !== undefined);

          let quotaSectionHtml = '';
          if (isApiKey) {
            if (acc.error_message) {
              quotaSectionHtml = `
                <div class="p-2.5 rounded-xl bg-red-500/10 border border-red-500/30 text-xs space-y-1">
                  <div class="text-red-400 font-semibold flex items-center">
                    <i class="fa-solid fa-triangle-exclamation mr-1.5"></i> Key Expired / Invalid
                  </div>
                  <p class="text-[10px] text-red-300/90 leading-tight">
                    Get a permanent key (AIzaSy...) at <a href="https://aistudio.google.com/app/apikey" target="_blank" class="underline text-indigo-300">aistudio.google.com</a>
                  </p>
                </div>
              `;
            } else {
              quotaSectionHtml = `
                <div class="p-2.5 rounded-xl bg-slate-900 border border-slate-800 text-xs flex items-center justify-between">
                  <span class="text-slate-400 flex items-center">
                    <i class="fa-solid fa-key text-amber-400 mr-2"></i> Google AI Studio Key
                  </span>
                  <span class="text-emerald-400 font-semibold">Active · PayG</span>
                </div>
              `;
            }
          } else {
            quotaSectionHtml = `
              <div class="grid grid-cols-2 gap-2 text-xs pt-1">
                <div class="p-2.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1.5 flex flex-col justify-between">
                  <div>
                    <div class="flex justify-between text-[11px] items-center">
                      <span class="text-slate-400">Gemini</span>
                      <span class="font-bold ${geminiIsExhausted ? 'text-red-400' : 'text-slate-200'}">${geminiPct}%</span>
                    </div>
                    <div class="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden mt-1">
                      <div class="h-full ${geminiIsExhausted ? 'bg-red-500' : (geminiPct > 30 ? 'bg-indigo-500' : 'bg-amber-500')}" style="width: ${geminiPct}%"></div>
                    </div>
                  </div>
                  <div class="text-[10px] text-right">
                    ${geminiIsExhausted
                      ? `<span class="text-red-400 font-medium"><i class="fa-solid fa-triangle-exclamation mr-1"></i>${geminiReset ? 'Resets ' + geminiReset : 'Exhausted'}</span>`
                      : `<span class="text-slate-400">${geminiReset ? 'Resets ' + geminiReset : 'Weekly limit'}</span>`}
                  </div>
                </div>

                <div class="p-2.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1.5 flex flex-col justify-between">
                  <div>
                    <div class="flex justify-between text-[11px] items-center">
                      <span class="text-slate-400">Claude / 3P</span>
                      <span class="font-bold ${claudeIsExhausted ? 'text-red-400' : 'text-slate-200'}">${claudePct}%</span>
                    </div>
                    <div class="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden mt-1">
                      <div class="h-full ${claudeIsExhausted ? 'bg-red-500' : (claudePct > 30 ? 'bg-purple-500' : 'bg-amber-500')}" style="width: ${claudePct}%"></div>
                    </div>
                  </div>
                  <div class="text-[10px] text-right">
                    ${claudeIsExhausted
                      ? `<span class="text-red-400 font-medium"><i class="fa-solid fa-triangle-exclamation mr-1"></i>${claudeReset ? 'Resets ' + claudeReset : '5h limit'}</span>`
                      : `<span class="text-slate-400">${claudeReset ? 'Resets ' + claudeReset : '5h rolling'}</span>`}
                  </div>
                </div>
              </div>
            `;
          }

          html += `
            <div class="p-5 rounded-2xl border transition space-y-3 relative overflow-hidden ${
              isEnabled
                ? 'bg-dark-card border-dark-border shadow-sm'
                : 'bg-slate-900/40 border-slate-800/80 opacity-60'
            }">
              <div class="flex items-center justify-between">
                <div class="flex items-center space-x-3 min-w-0 flex-1 mr-2">
                  <img src="${acc.picture || 'https://lh3.googleusercontent.com/a/default-user=s96-c'}" class="w-9 h-9 rounded-full bg-slate-800 border border-slate-700 flex-shrink-0" onerror="this.src='https://ui-avatars.com/api/?name=' + encodeURIComponent('${(acc.name || 'User').replace(/'/g, "\\'")}')">
                  <div class="min-w-0 flex-1">
                    <div class="font-bold text-white text-sm flex items-center flex-wrap gap-1">
                      <span class="truncate max-w-[140px] sm:max-w-[180px]" title="${acc.name || acc.email}">${acc.name || acc.email}</span>
                      <button onclick="openRenameModal('${acc.account_id}', '${(acc.name || acc.email).replace(/'/g, "\\'")}')" class="text-slate-500 hover:text-indigo-400 p-0.5 transition" title="Rename account / key">
                        <i class="fa-solid fa-pen-to-square text-[11px]"></i>
                      </button>
                      ${acc.is_primary ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 font-semibold border border-indigo-500/30">Primary</span>' : ''}
                      ${!isEnabled ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-medium border border-slate-700">Paused</span>' : ''}
                    </div>
                    <div class="text-[11px] text-slate-400 font-mono truncate" title="${acc.email}">${acc.email}</div>
                  </div>
                </div>
                <div class="flex items-center space-x-1.5 flex-shrink-0">
                  <button onclick="toggleAccount('${acc.account_id}', ${!isEnabled})" class="relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${isEnabled ? 'bg-indigo-600' : 'bg-slate-700'}" title="${isEnabled ? 'Pause account' : 'Enable account'}">
                    <span class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${isEnabled ? 'translate-x-4' : 'translate-x-0'}"></span>
                  </button>
                  <button onclick="deleteAccount('${acc.account_id}')" class="text-slate-500 hover:text-red-400 text-xs p-1.5 rounded-lg hover:bg-slate-800 transition" title="Remove account"><i class="fa-solid fa-trash-can"></i></button>
                </div>
              </div>

              ${quotaSectionHtml}

              <div class="flex items-center justify-between text-[11px] text-slate-500 pt-1 border-t border-slate-800/50">
                <span>Project: <code class="text-slate-400">${acc.project_id || 'default'}</code></span>
                <span>Reqs: <b class="text-slate-300">${acc.total_requests || 0}</b></span>
              </div>
            </div>
          `;
        });
        html += '</div>';
        container.innerHTML = html;
      } else {
        // --- COMPACT TABLE VIEW ---
        let html = `
          <div class="overflow-x-auto rounded-2xl border border-dark-border bg-dark-card shadow-sm custom-scrollbar">
            <table class="w-full text-left text-xs">
              <thead class="bg-slate-900/80 text-slate-400 border-b border-dark-border uppercase font-mono text-[10px]">
                <tr>
                  <th class="px-4 py-3">Account / Identity</th>
                  <th class="px-3 py-3">Type</th>
                  <th class="px-4 py-3">Gemini Quota</th>
                  <th class="px-4 py-3">Claude / 3P Quota</th>
                  <th class="px-3 py-3 text-center">Reqs</th>
                  <th class="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-800/80 text-slate-300 font-sans">
        `;

        filtered.forEach(acc => {
          const isEnabled = acc.enabled !== false;
          const isApiKey = acc.auth_method === 'api_key';
          const qDetails = acc.quota_details || {};
          const geminiQ = qDetails.gemini || { percent: 100, fraction: 1.0 };
          const claudeQ = qDetails['3p'] || qDetails.claude || { percent: 100, fraction: 1.0 };
          const geminiPct = Math.round(geminiQ.percent ?? (geminiQ.fraction * 100));
          const claudePct = Math.round(claudeQ.percent ?? (claudeQ.fraction * 100));
          const geminiReset = formatResetTime(geminiQ.reset_time);
          const claudeReset = formatResetTime(claudeQ.reset_time);
          const geminiIsExhausted = geminiPct <= 0 || (acc.rate_limited_models && acc.rate_limited_models.gemini !== undefined);
          const claudeIsExhausted = claudePct <= 0 || (acc.rate_limited_models && acc.rate_limited_models['3p'] !== undefined);

          html += `
            <tr class="hover:bg-slate-800/40 transition ${!isEnabled ? 'opacity-50' : ''}">
              <td class="px-4 py-3">
                <div class="flex items-center space-x-2.5">
                  <img src="${acc.picture || 'https://lh3.googleusercontent.com/a/default-user=s96-c'}" class="w-7 h-7 rounded-full bg-slate-800 border border-slate-700 flex-shrink-0" onerror="this.src='https://ui-avatars.com/api/?name=' + encodeURIComponent('${(acc.name || 'User').replace(/'/g, "\\'")}')">
                  <div class="min-w-0">
                    <div class="font-bold text-white flex items-center space-x-1.5">
                      <span class="truncate max-w-[180px]" title="${acc.name || acc.email}">${acc.name || acc.email}</span>
                      <button onclick="openRenameModal('${acc.account_id}', '${(acc.name || acc.email).replace(/'/g, "\\'")}')" class="text-slate-500 hover:text-indigo-400 transition" title="Rename"><i class="fa-solid fa-pen-to-square text-[10px]"></i></button>
                      ${acc.is_primary ? '<span class="text-[9px] px-1 py-0.2 rounded bg-indigo-500/20 text-indigo-300 font-semibold border border-indigo-500/30">Primary</span>' : ''}
                    </div>
                    <div class="text-[10px] text-slate-400 font-mono truncate max-w-[200px]" title="${acc.email}">${acc.email}</div>
                  </div>
                </div>
              </td>

              <td class="px-3 py-3">
                ${isApiKey
                  ? '<span class="inline-flex items-center text-[10px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20"><i class="fa-solid fa-key mr-1"></i> API Key</span>'
                  : '<span class="inline-flex items-center text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20"><i class="fa-brands fa-google mr-1"></i> OAuth</span>'}
              </td>

              <td class="px-4 py-3 min-w-[140px]">
                ${isApiKey ? '<span class="text-emerald-400 font-semibold text-[11px]">PayG Active</span>' : `
                  <div>
                    <div class="flex justify-between items-center text-[10px] mb-1">
                      <span class="font-bold ${geminiIsExhausted ? 'text-red-400' : 'text-slate-300'}">${geminiPct}%</span>
                      <span class="text-[9px] text-slate-500">${geminiReset || ''}</span>
                    </div>
                    <div class="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
                      <div class="h-full ${geminiIsExhausted ? 'bg-red-500' : (geminiPct > 30 ? 'bg-indigo-500' : 'bg-amber-500')}" style="width: ${geminiPct}%"></div>
                    </div>
                  </div>
                `}
              </td>

              <td class="px-4 py-3 min-w-[140px]">
                ${isApiKey ? '<span class="text-slate-500 text-[11px]">—</span>' : `
                  <div>
                    <div class="flex justify-between items-center text-[10px] mb-1">
                      <span class="font-bold ${claudeIsExhausted ? 'text-red-400' : 'text-slate-300'}">${claudePct}%</span>
                      <span class="text-[9px] text-slate-500">${claudeReset || ''}</span>
                    </div>
                    <div class="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
                      <div class="h-full ${claudeIsExhausted ? 'bg-red-500' : (claudePct > 30 ? 'bg-purple-500' : 'bg-amber-500')}" style="width: ${claudePct}%"></div>
                    </div>
                  </div>
                `}
              </td>

              <td class="px-3 py-3 text-center font-mono font-bold text-slate-300">
                ${acc.total_requests || 0}
              </td>

              <td class="px-4 py-3 text-right">
                <div class="flex items-center justify-end space-x-2">
                  <button onclick="toggleAccount('${acc.account_id}', ${!isEnabled})" class="relative inline-flex h-4 w-7 flex-shrink-0 cursor-pointer rounded-full border border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${isEnabled ? 'bg-indigo-600' : 'bg-slate-700'}" title="${isEnabled ? 'Pause' : 'Enable'}">
                    <span class="pointer-events-none inline-block h-3 w-3 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${isEnabled ? 'translate-x-3' : 'translate-x-0'}"></span>
                  </button>
                  <button onclick="deleteAccount('${acc.account_id}')" class="text-slate-500 hover:text-red-400 p-1 transition" title="Delete"><i class="fa-solid fa-trash-can text-xs"></i></button>
                </div>
              </td>
            </tr>
          `;
        });

        html += `
              </tbody>
            </table>
          </div>
        `;
        container.innerHTML = html;
      }
    }

    let currentRenameAccountId = null;

    function openRenameModal(accountId, currentName) {
      currentRenameAccountId = accountId;
      const inp = document.getElementById('renameInput');
      if (inp) {
        inp.value = currentName;
      }
      document.getElementById('renameModal')?.classList.remove('hidden');
      setTimeout(() => {
        inp?.focus();
        inp?.select();
      }, 50);
    }

    function closeRenameModal() {
      currentRenameAccountId = null;
      document.getElementById('renameModal')?.classList.add('hidden');
    }

    async function submitRenameModal() {
      if (!currentRenameAccountId) return;
      const newName = document.getElementById('renameInput')?.value?.trim();
      if (!newName) return;

      try {
        const res = await fetch(`/api/accounts/${currentRenameAccountId}/rename`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: newName })
        });
        if (res.ok) {
          closeRenameModal();
          await loadAccounts();
        } else {
          alert('Failed to rename account');
        }
      } catch (err) {
        console.error('Rename error:', err);
      }
    }

    let allModelsData = {};

    function renderModelCatalog(filterQuery = '') {
      const container = document.getElementById('modelList');
      const q = filterQuery.trim().toLowerCase();

      const totalCount = Object.keys(allModelsData).length;

      if (totalCount === 0) {
        document.getElementById('modelCount').innerText = '0 active models';
        container.innerHTML = `<div class="text-center py-8 text-slate-500 text-xs leading-relaxed">
          <i class="fa-solid fa-circle-pause text-amber-400/80 text-xl mb-2 block"></i>
          All accounts in the pool are paused.<br>
          <span class="text-slate-400">Enable at least one account to activate models.</span>
        </div>`;
        return;
      }

      const filteredEntries = Object.entries(allModelsData).filter(([key, info]) => {
        if (!q) return true;
        const name = (info.displayName || '').toLowerCase();
        return key.toLowerCase().includes(q) || name.includes(q);
      });

      document.getElementById('modelCount').innerText = q 
        ? `${filteredEntries.length} / ${totalCount} active` 
        : `${totalCount} active model${totalCount === 1 ? '' : 's'}`;

      if (filteredEntries.length === 0) {
        container.innerHTML = `<div class="text-center py-8 text-slate-500 text-xs">No active models found matching "<span class="text-slate-300 font-semibold">${filterQuery}</span>"</div>`;
        return;
      }

      container.innerHTML = '';
      const select = document.getElementById('modelSelect');

      for (const [key, info] of filteredEntries) {
        const poolQuota = info.pool_remaining_fraction !== undefined 
          ? Math.round(info.pool_remaining_fraction * 100) 
          : (info.quotaInfo && info.quotaInfo.remainingFraction !== undefined ? Math.round(info.quotaInfo.remainingFraction * 100) : 100);
        
        const quotaColor = poolQuota > 50 ? 'bg-emerald-500' : (poolQuota > 10 ? 'bg-amber-500' : 'bg-red-500');
        const readyAccs = info.available_accounts !== undefined ? info.available_accounts : 0;
        const totalAccs = info.total_accounts || 1;
        const resetStr = formatResetTime(info.quotaInfo?.resetTime);

        const item = document.createElement('div');
        item.className = 'p-3 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-700 transition cursor-pointer text-xs space-y-1.5';
        item.onclick = () => {
          if (select) select.value = key;
          document.getElementById('activeModelBadge').innerText = key;
        };

        // Build per-account mini badges with individual quotas and reset timers
        let accountBreakdownHtml = '';
        if (info.accounts && Object.keys(info.accounts).length > 0) {
          const badges = Object.entries(info.accounts).map(([email, accData]) => {
            const accPct = Math.round(accData.remainingFraction * 100);
            const accReset = formatResetTime(accData.resetTime);
            const dotColor = accPct > 50 ? 'bg-emerald-400' : (accPct > 0 ? 'bg-amber-400' : 'bg-red-500');
            const shortEmail = email.split('@')[0];
            const tooltip = `${email}: ${accPct}%${accReset ? ' (Resets ' + accReset + ')' : ''}`;
            return `<span class="inline-flex items-center text-[10px] text-slate-400 bg-slate-950 px-1.5 py-0.5 rounded border border-slate-800" title="${tooltip}">
              <span class="w-1.5 h-1.5 rounded-full ${dotColor} mr-1"></span>${shortEmail}: ${accPct}%
            </span>`;
          }).join(' ');
          accountBreakdownHtml = `<div class="flex flex-wrap gap-1 pt-1 border-t border-slate-800/60">${badges}</div>`;
        }

        item.innerHTML = `
          <div class="flex items-center justify-between font-semibold text-slate-200">
            <span>${info.displayName || key}</span>
            <span class="font-mono text-[10px] text-slate-400">${key}</span>
          </div>
          <div class="flex items-center justify-between text-[11px] text-slate-400">
            <span class="flex items-center space-x-1.5">
              <span class="font-bold ${poolQuota > 0 ? 'text-indigo-300' : 'text-red-400'}">Pool: ${poolQuota}%</span>
              <span class="text-[10px] ${readyAccs > 0 ? 'text-slate-500' : 'text-red-400 font-medium'}">(${readyAccs}/${totalAccs} ready)</span>
            </span>
            <span>${resetStr ? `<span class="text-amber-400/90 text-[10px] font-mono mr-2"><i class="fa-regular fa-clock mr-0.5"></i>${resetStr}</span>` : ''}Max: ${(info.maxTokens || 0).toLocaleString()} tok</span>
          </div>
          <div class="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
            <div class="h-full ${quotaColor}" style="width: ${poolQuota}%"></div>
          </div>
          ${accountBreakdownHtml}
        `;
        container.appendChild(item);
      }
    }

    function filterModelCatalog() {
      const q = document.getElementById('modelSearchInput')?.value || '';
      renderModelCatalog(q);
    }

    async function loadModels() {
      try {
        const res = await fetch('/api/models');
        const data = await res.json();
        allModelsData = data.models || {};
        const count = Object.keys(allModelsData).length;

        const select = document.getElementById('modelSelect');
        if (select) {
          if (count > 0) {
            select.innerHTML = '';
            for (const [key, info] of Object.entries(allModelsData)) {
              const opt = document.createElement('option');
              opt.value = key;
              opt.innerText = `${info.displayName || key} (${key})`;
              select.appendChild(opt);
            }
            if (allModelsData['gemini-3.7-flash-high']) {
              select.value = 'gemini-3.7-flash-high';
              document.getElementById('activeModelBadge').innerText = 'gemini-3.7-flash-high';
            } else {
              const firstKey = Object.keys(allModelsData)[0];
              select.value = firstKey;
              document.getElementById('activeModelBadge').innerText = firstKey;
            }
          } else {
            select.innerHTML = '<option value="">No active models (all accounts paused)</option>';
            document.getElementById('activeModelBadge').innerText = 'None';
          }
        }

        const currentFilter = document.getElementById('modelSearchInput')?.value || '';
        renderModelCatalog(currentFilter);
      } catch (err) {
        console.error("Models load failed", err);
      }
    }

    let currentAddTab = 'oauth';

    function switchAddTab(tab) {
      currentAddTab = tab;
      const btnOAuth = document.getElementById('tabBtnOAuth');
      const btnApiKey = document.getElementById('tabBtnApiKey');
      const contentOAuth = document.getElementById('tabContentOAuth');
      const contentApiKey = document.getElementById('tabContentApiKey');

      if (tab === 'oauth') {
        btnOAuth.className = 'flex-1 py-2 rounded-lg font-semibold transition bg-indigo-600 text-white shadow';
        btnApiKey.className = 'flex-1 py-2 rounded-lg font-semibold transition text-slate-400 hover:text-white';
        contentOAuth.classList.remove('hidden');
        contentApiKey.classList.add('hidden');
      } else {
        btnApiKey.className = 'flex-1 py-2 rounded-lg font-semibold transition bg-indigo-600 text-white shadow';
        btnOAuth.className = 'flex-1 py-2 rounded-lg font-semibold transition text-slate-400 hover:text-white';
        contentApiKey.classList.remove('hidden');
        contentOAuth.classList.add('hidden');
      }
    }

    async function openAddAccountModal() {
      switchAddTab('oauth');
      try {
        const res = await fetch('/api/accounts/oauth/start', { method: 'POST' });
        const data = await res.json();
        currentOAuthState = data.state;
        currentOAuthVerifier = data.code_verifier;
        document.getElementById('oauthLoginLink').href = data.auth_url;
        document.getElementById('authCodeInput').value = '';
        document.getElementById('apiKeyValueInput').value = '';
        document.getElementById('apiKeyLabelInput').value = '';
        document.getElementById('addAccountModal').classList.remove('hidden');
      } catch (e) {
        alert('Failed to start OAuth: ' + e);
      }
    }

    function closeAddAccountModal() {
      document.getElementById('addAccountModal').classList.add('hidden');
    }

    async function submitAddAccountModal() {
      if (currentAddTab === 'apikey') {
        const apiKey = document.getElementById('apiKeyValueInput').value.trim();
        const label = document.getElementById('apiKeyLabelInput').value.trim() || 'Gemini API Key';
        if (!apiKey) {
          alert('Please enter your Gemini API Key.');
          return;
        }

        const btn = document.getElementById('submitAuthBtn');
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Adding...';

        try {
          const res = await fetch('/api/accounts/apikey', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: apiKey, name: label })
          });
          if (!res.ok) {
            const errText = await res.text();
            throw new Error(errText);
          }
          const data = await res.json();
          alert('Successfully added API Key: ' + (data.name || data.email));
          closeAddAccountModal();
          await loadAccounts();
          await loadModels();
        } catch (err) {
          alert('Failed to add API key: ' + err.message);
        } finally {
          btn.disabled = false;
          btn.innerHTML = '<i class="fa-solid fa-check"></i> Add to Pool';
        }
        return;
      }

      // OAuth flow
      const codeOrUrl = document.getElementById('authCodeInput').value.trim();
      if (!codeOrUrl) {
        alert('Please paste the authorization code or redirect URL.');
        return;
      }

      const btn = document.getElementById('submitAuthBtn');
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Adding...';

      try {
        const res = await fetch('/api/accounts/oauth/callback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: codeOrUrl,
            state: currentOAuthState,
            code_verifier: currentOAuthVerifier,
          })
        });
        if (!res.ok) {
          const errText = await res.text();
          throw new Error(errText);
        }
        const data = await res.json();
        alert('Successfully added Google Account: ' + (data.email || data.account_id));
        closeAddAccountModal();
        await loadAccounts();
        await loadModels();
      } catch (err) {
        alert('Failed to add account: ' + err.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-check"></i> Add to Pool';
      }
    }

    async function toggleAccount(accId, newState) {
      try {
        const res = await fetch(`/api/accounts/${accId}/toggle`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: newState })
        });
        if (!res.ok) throw new Error(await res.text());
        await loadAccounts();
        await loadModels();
      } catch (err) {
        alert('Toggle failed: ' + err.message);
      }
    }

    async function toggleAllAccounts(newState) {
      try {
        const res = await fetch('/api/accounts/toggle_all', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: newState })
        });
        if (!res.ok) throw new Error(await res.text());
        await loadAccounts();
        await loadModels();
      } catch (err) {
        alert('Toggle all failed: ' + err.message);
      }
    }

    async function deleteAccount(accId) {
      if (!confirm('Remove this account from the pool?')) return;
      try {
        await fetch(`/api/accounts/${accId}`, { method: 'DELETE' });
        await loadAccounts();
      } catch (e) {
        alert('Failed to remove account: ' + e);
      }
    }

    async function refreshAllAccounts() {
      const icon = document.getElementById('refreshIcon');
      icon.classList.add('fa-spin');
      try {
        await fetch('/api/accounts/refresh_all', { method: 'POST' });
        await loadAccounts();
        await loadModels();
      } catch (err) {
        alert('Refresh failed: ' + err);
      } finally {
        icon.classList.remove('fa-spin');
      }
    }

    function clearChat() {
      messagesHistory = [];
      document.getElementById('chatContainer').innerHTML = `
        <div class="text-center py-16 text-slate-500">
          <div class="w-12 h-12 mx-auto rounded-full bg-slate-800 flex items-center justify-center text-slate-400 mb-3">
            <i class="fa-solid fa-wand-magic-sparkles text-xl"></i>
          </div>
          <p class="text-sm font-medium">Chat cleared. Ready for new questions.</p>
        </div>
      `;
      document.getElementById('tokenUsageBadge').innerText = '';
    }

    async function sendMessage(e) {
      if (e) e.preventDefault();
      if (isGenerating) return;

      const input = document.getElementById('userInput');
      const text = input.value.trim();
      if (!text) return;

      input.value = '';

      if (messagesHistory.length === 0) {
        document.getElementById('chatContainer').innerHTML = '';
      }

      // Add user message
      messagesHistory.push({ role: 'user', content: text });
      appendMessageUI('user', text);

      // Prepare assistant bubble
      const assistantBubble = appendMessageUI('assistant', '');
      const contentElem = assistantBubble.querySelector('.msg-content');
      const thoughtElem = assistantBubble.querySelector('.msg-thought');

      const model = document.getElementById('modelSelect').value;
      const system = document.getElementById('systemPrompt').value.trim();
      const temperature = parseFloat(document.getElementById('tempSlider').value);
      const maxTokens = parseInt(document.getElementById('maxTokens').value);
      const stream = document.getElementById('streamToggle').checked;

      const reqMessages = [];
      if (system) {
        reqMessages.push({ role: 'system', content: system });
      }
      reqMessages.push(...messagesHistory);

      isGenerating = true;
      document.getElementById('sendBtn').disabled = true;

      let fullText = '';
      let fullThought = '';

      try {
        if (stream) {
          const resp = await fetch('/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              model: model,
              messages: reqMessages,
              temperature: temperature,
              max_tokens: maxTokens,
              stream: true,
            })
          });

          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\\n');
            buffer = lines.pop();

            for (const line of lines) {
              const trimmed = line.trim();
              if (trimmed.startsWith('data:')) {
                const dataStr = trimmed.slice(5).trim();
                if (dataStr === '[DONE]') break;
                try {
                  const chunk = JSON.parse(dataStr);
                  const delta = chunk.choices?.[0]?.delta || {};

                  if (delta.reasoning_content) {
                    fullThought += delta.reasoning_content;
                    thoughtElem.classList.remove('hidden');
                    thoughtElem.querySelector('.thought-content').innerText = fullThought;
                  }

                  if (delta.content) {
                    fullText += delta.content;
                    contentElem.innerHTML = marked.parse(fullText);
                  }
                } catch (pe) {}
              }
            }
            document.getElementById('chatContainer').scrollTop = document.getElementById('chatContainer').scrollHeight;
          }
        } else {
          const resp = await fetch('/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              model: model,
              messages: reqMessages,
              temperature: temperature,
              max_tokens: maxTokens,
              stream: false,
            })
          });
          const resJson = await resp.json();
          const msg = resJson.choices?.[0]?.message || {};
          if (msg.reasoning_content) {
            thoughtElem.classList.remove('hidden');
            thoughtElem.querySelector('.thought-content').innerText = msg.reasoning_content;
          }
          fullText = msg.content || '';
          contentElem.innerHTML = marked.parse(fullText);
        }

        messagesHistory.push({ role: 'assistant', content: fullText });
        await loadAccounts(); // Update live quota and request counts
      } catch (err) {
        contentElem.innerHTML = `<span class="text-red-400">Error generating response: ${err.message}</span>`;
      } finally {
        isGenerating = false;
        document.getElementById('sendBtn').disabled = false;
      }
    }

    function appendMessageUI(role, text) {
      const container = document.getElementById('chatContainer');
      const msgDiv = document.createElement('div');
      msgDiv.className = `flex ${role === 'user' ? 'justify-end' : 'justify-start'}`;

      if (role === 'user') {
        msgDiv.innerHTML = `
          <div class="max-w-2xl px-5 py-3 rounded-2xl bg-indigo-600 text-white text-sm shadow-md">
            <p class="whitespace-pre-wrap">${text.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</p>
          </div>
        `;
      } else {
        msgDiv.innerHTML = `
          <div class="max-w-3xl w-full space-y-2">
            <div class="msg-thought hidden p-3 rounded-xl thought-bubble text-xs text-indigo-300 font-mono">
              <div class="font-bold text-indigo-400 mb-1 flex items-center">
                <i class="fa-solid fa-brain mr-1.5"></i> Thinking Process
              </div>
              <div class="thought-content max-h-48 overflow-y-auto whitespace-pre-wrap custom-scrollbar"></div>
            </div>
            <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 text-slate-100 text-sm prose prose-invert max-w-none shadow-md">
              <div class="msg-content">${text ? marked.parse(text) : '<span class="text-slate-500 animate-pulse">Generating response...</span>'}</div>
            </div>
          </div>
        `;
      }

      container.appendChild(msgDiv);
      container.scrollTop = container.scrollHeight;
      return msgDiv;
    }

    // Update Checker
    let cachedUpdateData = null;

    async function checkForSoftwareUpdates(force = false) {
      try {
        const res = await fetch(`/api/version${force ? '?force=true' : ''}`);
        const data = await res.json();
        cachedUpdateData = data;

        if (data.has_update) {
          const badgeCont = document.getElementById('updateBadgeContainer');
          const badgeText = document.getElementById('updateBadgeText');
          const banner = document.getElementById('updateBanner');
          const bannerNewVer = document.getElementById('bannerNewVer');
          const bannerName = document.getElementById('bannerReleaseName');

          if (badgeCont && badgeText) {
            badgeText.innerText = `Update v${data.latest_version}`;
            badgeCont.classList.remove('hidden');
          }

          if (banner && !sessionStorage.getItem('agy_update_banner_dismissed')) {
            bannerNewVer.innerText = `v${data.latest_version}`;
            bannerName.innerText = data.release_name || 'A new release is available on GitHub.';
            banner.classList.remove('hidden');
          }
        }
      } catch (err) {
        console.debug('Update check failed:', err);
      }
    }

    function dismissUpdateBanner() {
      document.getElementById('updateBanner')?.classList.add('hidden');
      sessionStorage.setItem('agy_update_banner_dismissed', 'true');
    }

    function openUpdateModal() {
      if (!cachedUpdateData) return;
      const sub = document.getElementById('updateModalSub');
      const title = document.getElementById('modalReleaseTitle');
      const body = document.getElementById('modalReleaseBody');
      const link = document.getElementById('modalGitHubLink');
      const gitSection = document.getElementById('updateActionGit');

      if (sub) sub.innerText = `v${cachedUpdateData.current_version} → v${cachedUpdateData.latest_version}`;
      if (title) title.innerText = cachedUpdateData.release_name || `Release v${cachedUpdateData.latest_version}`;
      if (body) body.innerText = cachedUpdateData.release_notes || 'No release description provided.';
      if (link) link.href = cachedUpdateData.release_url || 'https://github.com/kqlio67/agy-proxy/releases';

      if (gitSection) {
        if (cachedUpdateData.is_git_repo) {
          gitSection.classList.remove('hidden');
        } else {
          gitSection.classList.add('hidden');
        }
      }

      document.getElementById('updateModal')?.classList.remove('hidden');
    }

    function closeUpdateModal() {
      document.getElementById('updateModal')?.classList.add('hidden');
    }

    async function executeGitPull() {
      const btn = document.getElementById('btnGitPull');
      const out = document.getElementById('gitPullOutput');
      if (!btn || !out) return;

      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Pulling latest changes...';
      out.classList.remove('hidden');
      out.innerText = 'Running: git pull origin main...';

      try {
        const res = await fetch('/api/update/pull', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          out.innerText = `Successfully updated!\n${data.stdout || 'Already up to date.'}\n\nPlease restart the proxy server if python files changed.`;
          btn.innerHTML = '<i class="fa-solid fa-check"></i> Updated';
          setTimeout(() => {
            checkForSoftwareUpdates(true);
          }, 2000);
        } else {
          out.innerText = `Update failed (code ${data.returncode}):\n${data.stderr || data.stdout || data.error}`;
          btn.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Retry';
          btn.disabled = false;
        }
      } catch (err) {
        out.innerText = `Network/Server error: ${err.message}`;
        btn.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Retry';
        btn.disabled = false;
      }
    }

    async function copySnippet(elementId, btn) {
      const textElem = document.getElementById(elementId);
      if (!textElem) return;
      const text = textElem.innerText || textElem.textContent;
      try {
        await navigator.clipboard.writeText(text);
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-check text-emerald-400"></i> <span class="text-emerald-300">Copied!</span>';
        btn.classList.add('bg-emerald-600/30', 'border-emerald-500/50');
        setTimeout(() => {
          btn.innerHTML = originalHtml;
          btn.classList.remove('bg-emerald-600/30', 'border-emerald-500/50');
        }, 1800);
      } catch (e) {
        console.error('Copy failed:', e);
      }
    }

    // Init
    loadAccounts();
    loadModels();
    checkForSoftwareUpdates();
  </script>
</body>
</html>
"""
