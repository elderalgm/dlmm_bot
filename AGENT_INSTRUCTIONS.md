# 🤖 AI Agent Quick-Start & Setup Guide

This document is specifically written for AI Coding Agents (such as Cursor, Claude Code, Antigravity, Devin, etc.) to automate the deployment, setup, and execution of this Solana Meteora DLMM Trading Bot.

---

## 📌 Project Overview
* **Language Stack:** Python 3.10+ (Core Bot Logic & Technical Analysis) + Node.js 20+ (Meteora DLMM SDK Bridge & On-chain execution).
* **Architecture:** Python runs the main scan/monitor loop and invokes `node meteora_bridge.js <command> [args]` via subprocess calls.
* **Process Management:** Managed via `pm2` using `ecosystem.config.js`.

---

## 🚀 Automated Installation Checklist for AI Agent

When instructed to "setup" or "install" this repository on a VPS or local environment, perform the following steps sequentially:

### Step 1: System Dependencies
Ensure Node.js (>=20) and Python (>=3.10) are installed:
```bash
node -v
python3 --version
```

### Step 2: Project Dependencies
Install Node.js and Python packages:
```bash
npm install
pip3 install -r requirements.txt
```

### Step 3: Configuration Setup
Check if `config.json` exists in the repository root.
If `config.json` does NOT exist:
1. Copy `config.example.json` to `config.json`:
   ```bash
   cp config.example.json config.json
   ```
2. Prompt the user to provide the required secret parameters:
   - `wallet_private_key`: Base58 private key of the Solana wallet.
   - `rpc_url`: Solana mainnet RPC URL (e.g. Helius, Quicknode).
   - `telegram_token`: Telegram Bot API token from @BotFather.
   - `chat_id`: Telegram chat ID for notifications.
   - `gmgn_api_key`: GMGN openapi key.
   - `jupiter_api_key`: Jupiter API key.

### Step 4: Verification & Execution
1. Test Python script syntax:
   ```bash
   python3 -m py_compile dlmm_bot.py
   ```
2. Test Node.js bridge balance command:
   ```bash
   node meteora_bridge.js get-balance
   ```
3. Launch process using PM2:
   ```bash
   pm2 start
   pm2 save
   pm2 startup
   ```

---

## 🔍 Key Strategy & Safety Parameters
* **Strategy Selection:** Dynamic. Uses `bid-ask` if `collect_fee_mode == 0` and `base_fee_pct < 5.0%`. Otherwise defaults to `spot`.
* **Range Placement:** Downside range is `-91%` from the active bin.
* **Safety Rules:** Enforces 14 GMGN safety checks (Renounced mint/freeze, MC >= $250k, Volume >= $1M, Holder count >= 900, Top 10 <= 30%, Bundler <= 68%, etc.).
* **Single Position Limit:** Automatically caps active positions to 1 at a time by depositing available wallet balance (minus gas reserve & rent).
