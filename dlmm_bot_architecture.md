# Solana Meteora DLMM Trading Bot - System Architecture & Feature Guide

This document provides a comprehensive technical overview of the Solana Meteora DLMM Trading Bot. It describes the system's architecture, key features, codebase, API integrations, and safety mechanics. This guide is designed to be easily ingested and understood by other AI agents or developers.

---

## 1. System Overview & Technology Stack

The bot is designed to perform automated and manual market-making on Solana using **Meteora's Dynamic Liquidity Market Maker (DLMM)** pools and **Jupiter Swap V2**. 

### Technology Stack:
*   **Core Controller (Python):** `dlmm_bot.py` handles the main loops, Telegram bot API integration, token rank scanning via GMGN, technical indicator calculations (using `pandas`), and local state management.
*   **Blockchain Bridge (Node.js):** `meteora_bridge.js` handles web3 operations using the official `@solana/web3.js` and `@meteora-ag/dlmm` SDKs. It signs and submits on-chain transactions, interacts with Jupiter Swap, and fetches account data.
*   **Communication Bridge:** Python spawns Node.js as a subprocess using `subprocess.run` to perform stateless blockchain operations, receiving responses in JSON.
*   **Server Host (Python):** `server.py` runs a lightweight HTTP health check server and acts as a process manager to keep `dlmm_bot.py` running 24/7 on Render.
*   **State Management:** State is tracked in `state.json` (active positions, trade history, RPC credit usage, and configuration overrides).
*   **User Interface:** A Telegram Bot provides an interactive Turkish control panel with custom keyboard buttons and real-time alerts.

---

## 2. Key Features & Components

### A. Automated Token Scanner (GMGN Rank)
*   **GMGN Rank Fetching:** Every 60 seconds, the bot queries the GMGN API for the top 100 Solana tokens sorted by 24h trading volume.
*   **Safety Filters:** Before considering a token, it checks:
    *   **Mint/Freeze Authority:** Must be renounced (`renounced_mint == 1` and `renounced_freeze_account == 1`).
    *   **Token Age:** Token must be at least 2 hours old.
    *   **Market Cap & Volume:** Market capitalization must be $\ge \$250,000$, and 24h volume $\ge \$1,000,000$.
    *   **Developer & Top Holders:** Top 10 holders rate $< 30\%$, Dev team hold rate $< 5\%$, and Bundler rate $< 68\%$.
    *   **Risk Scores:** Entrapment ratio $< 30\%$ and Rug ratio $< 50\%$.
    *   **ATH Proximity:** Current Market Cap must be $\ge 80\%$ of its historical highest Market Cap.
*   **ATH Breakout Detection:**
    *   Fetches 150 of 5-minute kline candles from GMGN.
    *   Identifies historical high price (ATH) in the last 50 candles.
    *   Triggers an entry signal if a candle closes above the ATH on significant volume (Breakout volume multiplier $\ge 1.1$).
    *   Excludes the active candle (`index=-1`) during historical checks to prevent repainting issues.

### B. Dynamic DLMM Pool Selection (Meteora)
*   **Pool Screening:** Fetches all available pools for the target token from Meteora's datapi.
*   **Filters:** Pools must meet:
    *   `collect_fee_mode == 1` (claims fees automatically).
    *   `bin_step >= 80` (optimal spacing).
    *   `base_fee_pct >= 2.0%` (minimum profitability).
*   **Prioritization:** Prefers pools meeting TVL $\ge \$15,000$ (with a soft warning fallback prioritizing the highest TVL pool).
*   **Position Placement:** Automatically deploys liquidity using the **Spot** strategy, placing the position starting from the active bin down to a safety downside percentage (default is 92%).

### C. Strict Uninitialized Bin Array Protection
*   **Problem:** If the bot opens a position in a range where the price bins are not yet initialized, Solana forces the transaction to initialize them. This incurs **non-refundable rent fees** (~0.04 to 0.07 SOL per bin array), which permanently drains the user's wallet.
*   **Automatic Scanner Block:** In `check_tokens`, the bot calls `check-range` and checks `missing_arrays`. If `missing_arrays > 0`, it skips the token immediately.
*   **Manual Buy Race Condition Protection:** During Telegram `/buy` flows, a final check is executed *immediately* before the on-chain `open` command is submitted. If the price shifted in the last second and a bin array is missing, the transaction is safely aborted.

### D. Active Position Price Monitoring & Technical Exits
*   Every 30 seconds, the bot monitors active positions.
*   **Rebalance Exit (Out of Range):** If the active bin moves upward out of the position's upper boundary (`active_bin > upper_bin`), the position is closed, and the bot scans again to rebalance.
*   **Technical Indicator Exits:**
    *   Fetches the 5-minute kline data.
    *   Calculates 4 indicators: **RSI(2)**, **MACD Histogram**, **Bollinger Bands(20, 2)**, and **Supertrend(10, 3)**.
    *   Evaluates them strictly on the **last completed candle** (`index=-2`) to prevent temporary price spikes from triggering premature exits.
    *   Triggers liquidation if at least **2 out of 4 indicators** flash exit signals:
        1.  `RSI(2) >= 90`
        2.  `MACD Histogram` crosses from red to green (first positive bar).
        3.  `Bollinger Upper Band` touch (high price exceeds BB upper band).
        4.  `Supertrend` flip from red to green or touch.
    *   **Safety Time Filter:** Exits are only allowed if the candle closed *after* the position was opened (`candle_close_time > opened_at`).

### E. Self-Healing Sync Loop (On-Chain Recovery)
*   **State Reconstruction:** Every 5 minutes, the bot runs `reconstruct_state_from_chain`.
*   **Direct Blockchain Auditing:** Queries the blockchain for all active positions owned by the user's wallet with the Meteora DLMM program ID (`LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo`).
*   **Auto-Healing:** If an on-chain position is found but is missing from `state.json` (due to server restarts, crashed web transactions, or local state wipe), the bot reconstructs the position metadata, recovers the symbol via DexScreener/GMGN, alerts the user, and starts tracking/monitoring it again.
*   **Cleanup:** Removes dead/closed positions from `state.json`.

### F. Leftover Token Cleanup Loop
*   **Autopilot Sweeper:** Every 10 minutes, the bot scans all SPL and Token-2022 accounts in the wallet (skipping WSOL).
*   **Jupiter Swap back to SOL:** If any residual tokens are found, it queries Jupiter Swap API and swaps them back to SOL automatically.

### G. Solana Network Congestion Protection
*   **Priority Fees:** Prepends `ComputeBudgetProgram.setComputeUnitLimit({ units: 800000 })` and `ComputeBudgetProgram.setComputeUnitPrice({ microLamports: 1500000 })` to all DLMM transactions to ensure they confirm during network congestion.
*   **Extended Timeout:** Increases Node.js bridge execution timeout to 90 seconds for all write operations (`open`, `close`, `swap`, `swap-remaining-tokens`).

### H. Helius RPC Credit Optimization & Monitoring
*   **Programmatic Usage Tracking:** Authenticates to Helius Dev Portal via wallet signature to monitor live monthly credit usage.
*   **Lightweight Active Bin Tracker:** Instead of the expensive `check-range` command (which makes multiple heavy RPC calls), active position tracking uses a custom `get-active-bin` command. This only fetches the pool state (1 `getAccountInfo` call, costing 5 credits instead of 25).
*   **Total Savings:** Cuts Helius RPC consumption by over **80%** (monthly idle sync costs reduced to ~216,000 credits), ensuring it runs comfortably under the 1,000,000 free monthly tier.
*   **Automatic Shutoff:** Stops scanning new tokens if monthly credit usage reaches 90%.

### I. Wallet Audit Tool (`analyze_wallet.py`)
*   A standalone script that queries the Solana blockchain for the wallet's last 25 transactions.
*   Uses a fallback list of public RPC nodes (`rpc.ankr.com`, `solana.publicnode.com`, etc.) with sleep delays to prevent rate limits.
*   Identifies transaction types and calculates the exact SOL balance changes to audit where funds/rents were spent and refunded.

---

## 3. Configuration & State Schemas

### `config.json`
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

### `state.json`
```json
{
  "active_positions": {
    "9zwhS3b1oYuUEqWNpu2SPkEH24JMVWFEVQvHuYXZpump": {
      "symbol": "HUNTER",
      "pool_address": "BRHNunv6MzznkDQAev7aEBmQANudm7NC44MuLVz6FMf6",
      "position_address": "DfoqJ3tskX61dGminfWHpB861NzdgJXF9dDsofLpe4tb",
      "lower_bin": 100000,
      "upper_bin": 100100,
      "deposit_sol": 0.1939,
      "refundable_rent": 0.25,
      "opened_at": 1780613312.654,
      "open_price": 0.00218,
      "reconstructed": false
    }
  },
  "history": [
    {
      "token_address": "DezXAZ8z7PnrnRJjz3wX4mRe9kxJgSG2cAhK8v3nw1gp",
      "symbol": "BONK",
      "pool_address": "85VBFQZG9AYbgU5CqXuhjcTyRs64od6e19yZ1p7jPTh3",
      "position_address": "...",
      "deposit_sol": 0.05,
      "refundable_rent": 0.25,
      "retrieved_sol": 0.298,
      "pnl": 0.048,
      "pnl_pct": 96.0,
      "closed_at": 1780613200.0,
      "reason": "exit_signal: RSI(2) >= 90, Bollinger Upper Band Touch"
    }
  ],
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

## 4. Key CLI Bridge Commands (`meteora_bridge.js`)

Other tools or scripts can interact with the blockchain bridge using:
*   `node meteora_bridge.js get-balance` -> Returns current wallet SOL balance.
*   `node meteora_bridge.js get-active-bin <pool_address>` -> Returns active bin ID and price.
*   `node meteora_bridge.js check-range <pool_address> [downside_pct]` -> Evaluates range rent, missing bin arrays, and price.
*   `node meteora_bridge.js open <pool_address> <amount_sol> [downside_pct] [strategy_type]` -> Deploys a new DLMM position.
*   `node meteora_bridge.js close <pool_address> <position_address>` -> Liquidates the DLMM position and returns token amounts.
*   `node meteora_bridge.js swap <input_mint> <output_mint> <amount>` -> Swaps tokens using Jupiter Swap V2.
*   `node meteora_bridge.js get-active-positions` -> Queries all active position accounts owned by the wallet on-chain.
*   `node meteora_bridge.js swap-remaining-tokens` -> Sweeps and cleans leftover token balances.
