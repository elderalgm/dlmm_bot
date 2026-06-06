# 🤖 Solana Meteora DLMM Trading Bot - Complete Technical Guide

This document is a comprehensive "from top to bottom" (tepeden tırnağa) technical guide for the Solana Meteora DLMM Trading Bot. It has been prepared specifically to explain the bot's features, directory structure, core functions, communication flows, and safety controls to developers or other AI agents.

---

## 📂 1. Directory Structure & File References

All key files and code components are organized under the following paths:

*   **Main Python Controller:** [dlmm_bot.py](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/dlmm_bot.py) — Handles Telegram bot handlers, GMGN scan loop, technical indicators logic, state orchestration, and status alerts.
*   **Web3 SDK Bridge (Node.js):** [meteora_bridge.js](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/meteora_bridge.js) — Integrates with `@meteora-ag/dlmm` and `@solana/web3.js` to construct, sign, and broadcast transactions, and fetch pool range information.
*   **Wallet Auditor Tool:** [analyze_wallet.py](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/analyze_wallet.py) — Independent utility to audit the wallet's last 25 transactions, parsing SOL/rent adjustments with public rate-limited fallbacks.
*   **Keep-Alive & Health Server:** [server.py](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/server.py) — Simple web server that listens on port `8000` to satisfy Render's health checks and auto-restarts the bot process if it halts.
*   **Settings File:** [config.json](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/config.json) — Local configuration parameters (API tokens, RPC endpoints, and trading parameters).
*   **State Tracker:** [state.json](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/state.json) — Keeps track of currently active positions, transaction history, bot pause status, and Helius credit consumption metrics.

---

## ⚙️ 2. Key Architecture: The Subprocess Bridge

Since Meteora's SDK lacks complete Python support, the bot uses a **bilingual subprocess bridge**:
1. [dlmm_bot.py](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/dlmm_bot.py) acts as the supervisor. Whenever it needs on-chain information or transaction execution, it invokes the Node bridge by spawning a subprocess:
   ```bash
   node meteora_bridge.js <command> [arguments]
   ```
2. The function [run_bridge()](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/dlmm_bot.py#L92) captures the standard output, cleans any warning logs, locates the valid JSON block returned, and parses it back into a Python dictionary.
3. Execution timeouts are dynamically set: **90 seconds** for writing operations (`open`, `close`, `swap`) to prevent hangs during network congestion, and **45 seconds** for reading operations.

---

## 🎯 3. Core Features & Trading Logic

### 🔍 A. Automated GMGN Token Scanner & Filters
Every 60 seconds, the scanner retrieves the top 100 Solana tokens sorted by 24h volume from GMGN API. Tokens must satisfy the following strict criteria:
*   **Contract Safety:** Freeze authority and Mint authority must be fully renounced.
*   **Age Limit:** Token age $\ge 2$ hours.
*   **Volume & Liquidity:** Market Cap $\ge \$250,000$ and 24h Volume $\ge \$1,000,000$.
*   **Distribution Rules:** Dev holding rate $< 5\%$, Top 10 Holders rate $< 30\%$, and Bundler rate $< 68\%$.
*   **Risk Metrics:** Entrapment ratio $< 30\%$ and Rug ratio $< 50\%$.
*   **ATH Breakout Entry Signal:**
    *   Fetches the last 150 of 5-minute candles.
    *   Finds the maximum price (ATH) across the last 50 candles (excluding the current active candle at index `-1` to prevent repainting issues).
    *   Triggers buying if the closing price breaks out above the historical ATH with a volume multiplier $\ge 1.1$.

### 🌊 B. Dynamic DLMM Pool Selection (Meteora)
Once a token passes safety and breakout filters, the bot searches for the best Meteora DLMM pool:
*   Must have `collect_fee_mode == 1` (auto-claims fees).
*   Must have `bin_step >= 80` (optimal price resolution).
*   Must have `base_fee_pct >= 2.0%` (lucrative fee generation).
*   **TVL Priority:** TVL must be $\ge \$15,000$ (with a soft fallback warning if the highest TVL pool is slightly lower but still viable).
*   **Position Placement Strategy:** Deploys liquidity using the **Spot** strategy, distributing positions from the active price bin down to the configured downside percentage (default: 92%).

### 🛡️ C. Strict Rent Protection (Bin Array Safety)
*   **The Problem:** Deploying liquidity to empty ranges requires creating and initializing new bin arrays on-chain. This locks non-refundable SOL rent (~0.04 to 0.07 SOL per bin array), permanently bleeding user funds.
*   **Automated Block:** The scanning loop calls [cmdCheckRange()](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/meteora_bridge.js#L80) via `check-range`. If `missing_arrays > 0`, the token is skipped automatically.
*   **Manual Buy Block:** During a manual `/buy` flow via Telegram, a final redundant check is run immediately before submitting the transaction. If the price shifted and a bin array is missing, the buy transaction is aborted before sending.

### 📈 D. Technical Indicators & Position Exits
Positions are monitored every 30 seconds. A position is closed immediately if:
1.  **Rebalance Exit:** The active price moves upward completely out of the position range (`active_bin > upper_bin`).
2.  **Technical Exit:** The bot calculates indicators on the **last completed candle (index `-2`)** to avoid false triggers during unclosed candles. An exit is triggered if at least **2 out of 4** indicators match:
    *   `RSI(2) >= 90` (Overbought).
    *   `MACD Histogram` shifts from red to green (bullish exhaustion/momentum shifts).
    *   `Bollinger Bands Upper Touch` (Price exceeds the upper band).
    *   `Supertrend (10, 3)` flip or touch.

### 🔄 E. Self-Healing State Recovery (Sync Loop)
*   Every 5 minutes, [reconstruct_state_from_chain()](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/dlmm_bot.py) is called.
*   It fetches all active position accounts owned by the wallet pointing to the Meteora DLMM program (`LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo`) directly from the blockchain.
*   If an on-chain position is found but is missing from [state.json](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/state.json) (due to a server crash, state corruption, or manual execution), the bot queries GMGN/DexScreener to resolve the symbol, reconstructs the position metadata, notifies the user, and resumes tracking and monitoring it.
*   Dead or closed positions are cleaned out from local tracking.

### 🧹 F. Leftover Token Sweeper Loop
*   Every 10 minutes, the bot inspects all SPL and Token-2022 token accounts in the wallet (ignoring WSOL).
*   If any dust or residual tokens are left over from closed positions, it uses Jupiter Swap V2 API to swap them back to SOL automatically.

### ⚡ G. Helius RPC Credit Optimization
*   Routine 30-second price monitoring is performed using a lightweight custom command [cmdGetActiveBin()](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/meteora_bridge.js) which only calls `getAccountInfo` for the pool (costing 5 Helius credits) instead of the full `check-range` command (costing 25 credits).
*   This optimization reduces the bot's idle credit usage by **80%**, keeping it safely below the **1,000,000 monthly Helius free tier**.
*   If the monthly credit usage exceeds 90%, the bot automatically pauses scanning to prevent overages.

---

## 🛠️ 4. Configuration Schemas

### `config.json` Reference
```json
{
  "rpc_url": "YOUR_SOLANA_RPC_URL",
  "wallet_private_key": "YOUR_WALLET_PRIVATE_KEY_BASE58",
  "telegram_token": "YOUR_TELEGRAM_BOT_TOKEN",
  "chat_id": "YOUR_TELEGRAM_CHAT_ID",
  "gmgn_api_key": "YOUR_GMGN_API_KEY",
  "dry_run": false,
  "gas_reserve": 0.05,
  "min_tvl": 15000,
  "min_bin_step": 80,
  "min_base_fee_pct": 2.0
}
```

### `state.json` Reference
```json
{
  "active_positions": {
    "TokenMintAddressHere": {
      "symbol": "TOKEN",
      "pool_address": "PoolAddressHere",
      "position_address": "PositionAddressHere",
      "lower_bin": 100000,
      "upper_bin": 100100,
      "deposit_sol": 0.1939,
      "refundable_rent": 0.25,
      "opened_at": 1780613312.654,
      "open_price": 0.00218,
      "reconstructed": false
    }
  },
  "history": [],
  "is_paused": false,
  "dry_run_override": null,
  "credits_tracked": {
    "month": "2026-06",
    "used": 15114,
    "limit": 1000000
  }
}
```

---

## 🎮 5. Commands Reference

### Telegram Command Console (Turkish Interface)
*   `/menu` — Displays the main menu keyboard.
*   `/status` — Shows current wallet balance, active positions count, and Helius credit usage.
*   `/positions` — Details active DLMM positions, current ranges, prices, and simulated/live PnL.
*   `/candidates` — Lists tokens currently passing all safety scans.
*   `/buy <pool_address>` — Starts manual position initialization.
*   `/close <symbol>` — Liquidates the active position and swaps the tokens to SOL.
*   `/toggle_mode` — Toggles between Live Mode (real transactions) and Simulation Mode (dry_run).
*   `/toggle_pause` — Pauses or resumes the token scanner.

### Node Bridge CLI Commands (`meteora_bridge.js`)
*   `get-balance` — Fetches current wallet balance in SOL.
*   `get-active-bin <pool_address>` — Fetches current active price bin and price.
*   `check-range <pool_address> [downside_pct]` — Checks price ranges, bin arrays, and rent.
*   `open <pool_address> <amount_sol> [downside_pct] [strategy_type]` — Swaps SOL, deploys liquidity, and returns position accounts.
*   `close <pool_address> <position_address>` — Liquifies the position and swaps token balances.
*   `swap <input_mint> <output_mint> <amount>` — Swaps tokens via Jupiter.
*   `get-active-positions` — Lists on-chain Meteora DLMM positions.
*   `swap-remaining-tokens` — Sweep leftover balances.

---

## 🔍 6. Independent Wallet Audits
The [analyze_wallet.py](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/analyze_wallet.py) tool is run via command line to check wallet activity:
```bash
python analyze_wallet.py <WALLET_ADDRESS>
```
*   Queries recent transactions from multiple public nodes with rate-limiting retry delays.
*   Parses transaction details to identify **Swaps, DLMM Position Opens, DLMM Position Closes, and Transfer** activities.
*   Displays exact SOL delta, fee costs, and rent refunds to verify balance fluctuations.
