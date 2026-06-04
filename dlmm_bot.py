import os
import sys
import time
import json
import uuid
import logging
import subprocess
import requests
import pandas as pd
import pandas_ta as ta

# Set console encoding to UTF-8 on Windows to prevent UnicodeEncodeError with emojis
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Config Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

HOST = "https://openapi.gmgn.ai"
CONFIG_PATH = "config.json"
STATE_PATH = "state.json"

# Load Configuration
def load_config():
    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            logging.error(f"Error loading config.json: {e}")
            
    # Load and merge with environment variables
    merged = {
        "rpc_url": os.environ.get("SOLANA_RPC_URL", config.get("rpc_url", "YOUR_SOLANA_RPC_URL")),
        "wallet_private_key": os.environ.get("WALLET_PRIVATE_KEY", config.get("wallet_private_key", "YOUR_WALLET_PRIVATE_KEY_BASE58")),
        "telegram_token": os.environ.get("TELEGRAM_TOKEN", config.get("telegram_token", "YOUR_TELEGRAM_BOT_TOKEN")),
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", config.get("chat_id", "YOUR_TELEGRAM_CHAT_ID")),
        "gmgn_api_key": os.environ.get("GMGN_API_KEY", config.get("gmgn_api_key", "gmgn_bd2444c0472e8194980316c0ce4077f5")),
        "dry_run": os.environ.get("DRY_RUN", "true").lower() == "true" if "DRY_RUN" in os.environ else config.get("dry_run", True),
        "gas_reserve": float(os.environ.get("GAS_RESERVE", config.get("gas_reserve", 0.05))),
        "min_tvl": float(os.environ.get("MIN_TVL", config.get("min_tvl", 15000))),
        "min_bin_step": int(os.environ.get("MIN_BIN_STEP", config.get("min_bin_step", 80))),
        "min_base_fee_pct": float(os.environ.get("MIN_BASE_FEE_PCT", config.get("min_base_fee_pct", 2.0)))
    }
    return merged

# Load / Save State
def load_state():
    if not os.path.exists(STATE_PATH):
        return {"active_positions": {}, "history": []}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active_positions": {}, "history": []}

def save_state(state):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save state: {e}")

# Bridge Invoker
def run_bridge(cmd_args):
    try:
        res = subprocess.run(
            ["node", "meteora_bridge.js"] + cmd_args,
            capture_output=True,
            text=True,
            timeout=45
        )
        output = res.stdout.strip()
        # Look for the last line starting with { and ending with } to avoid warnings
        for line in reversed(output.split('\n')):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        logging.error(f"Bridge did not return JSON. Stdout: {output}, Stderr: {res.stderr}")
        return {"success": False, "error": f"Invalid bridge response: {output}"}
    except Exception as e:
        logging.error(f"Failed to execute bridge: {e}")
        return {"success": False, "error": str(e)}

# Telegram Notification
def send_telegram(config, text):
    token = config.get("telegram_token")
    chat_id = config.get("chat_id")
    if not token or not chat_id or token.startswith("YOUR_"):
        return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Failed to send Telegram notification: {e}")

# GMGN API Auth Query Builder
def build_auth_query(config):
    return {
        "timestamp": int(time.time()),
        "client_id": str(uuid.uuid4())
    }

# ─── Indicators Calculation ──────────────────────────────────
def calculate_indicators(klines):
    if len(klines) < 30:
        return None
    
    df = pd.DataFrame(klines)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
        
    df['RSI_2'] = ta.rsi(df['close'], length=2)
    
    macd = ta.macd(df['close'])
    if macd is not None and not macd.empty:
        hist_col = [c for c in macd.columns if 'MACDh' in c][0]
        df['MACD_hist'] = macd[hist_col]
    else:
        df['MACD_hist'] = 0.0

    bbands = ta.bbands(df['close'], length=20, std=2)
    if bbands is not None and not bbands.empty:
        upper_bb_col = [c for c in bbands.columns if 'BBU' in c][0]
        df['BB_upper'] = bbands[upper_bb_col]
    else:
        df['BB_upper'] = float('inf')
        
    st = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=3)
    if st is not None and not st.empty:
        dir_col = [c for c in st.columns if 'SUPERTd' in c][0]
        val_col = [c for c in st.columns if c.startswith('SUPERT_')][0]
        df['ST_dir'] = st[dir_col]
        df['ST_val'] = st[val_col]
    else:
        df['ST_dir'] = 1
        df['ST_val'] = 0.0
        
    return df

# ─── Signal Evaluation ────────────────────────────────────────
def check_exit_signal(df, index=-1):
    """ Returns True if at least 2 out of 4 exit indicators trigger """
    if df is None or len(df) < index * -1:
        return False
    
    last = df.iloc[index]
    prev = df.iloc[index - 1]
    
    triggers = []
    
    # 1. RSI(2) >= 90
    if pd.notna(last['RSI_2']) and last['RSI_2'] >= 90:
        triggers.append("RSI(2) >= 90")
        
    # 2. MACD Red to Green (first green bar)
    if pd.notna(last['MACD_hist']) and pd.notna(prev['MACD_hist']):
        if prev['MACD_hist'] <= 0 and last['MACD_hist'] > 0:
            triggers.append("MACD Red -> Green")
            
    # 3. Bollinger Band touch/close above upper band
    if pd.notna(last['BB_upper']):
        if last['high'] >= last['BB_upper'] or last['close'] > last['BB_upper']:
            triggers.append("Bollinger Upper Band Touch")
            
    # 4. Supertrend touch/flip
    if pd.notna(last['ST_dir']):
        if last['ST_dir'] == -1 and last['high'] >= last['ST_val']:
            triggers.append("Supertrend Red Line Touch")
        elif prev['ST_dir'] == -1 and last['ST_dir'] == 1:
            triggers.append("Supertrend Red -> Green")
            
    return len(triggers) >= 2, triggers

# ─── Fetch Kline Data ─────────────────────────────────────────
def get_kline_data(config, token_address):
    query = build_auth_query(config)
    query["chain"] = "sol"
    query["address"] = token_address
    query["resolution"] = "5m"
    query["limit"] = "150"
    
    headers = {
        "X-APIKEY": config["gmgn_api_key"],
        "Content-Type": "application/json"
    }
    
    url = f"{HOST}/v1/market/token_kline"
    try:
        res = requests.get(url, params=query, headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            if data.get("code") == 0:
                return data.get("data", {}).get("list", [])
        elif res.status_code == 429:
            logging.warning("GMGN API Rate limit hit.")
    except Exception as e:
        logging.error(f"Error fetching kline for {token_address}: {e}")
    return []

# ─── Fetch Pools from Meteora ─────────────────────────────────
def fetch_meteora_pools(token_address):
    url = f"https://dlmm.datapi.meteora.ag/pools?query={token_address}"
    try:
        res = requests.get(url, timeout=15)
        if res.status_code == 200:
            data = res.json()
            pools = data.get("data", []) or data
            if isinstance(pools, dict) and "pools" in pools:
                return pools["pools"]
            elif isinstance(pools, dict) and "data" in pools:
                return pools["data"]
            return pools
    except Exception as e:
        logging.error(f"Error fetching pools from Meteora: {e}")
    return []

# ─── Screen Pool ──────────────────────────────────────────────
def select_best_pool(config, pools):
    eligible_pools = []
    
    min_tvl = config.get("min_tvl", 15000)
    min_bin_step = config.get("min_bin_step", 80)
    min_fee = config.get("min_base_fee_pct", 2.0)
    
    for p in pools:
        cfg = p.get("pool_config", {})
        tvl = p.get("tvl", p.get("liquidity", 0))
        bin_step = cfg.get("bin_step", 0)
        base_fee = cfg.get("base_fee_pct", 0)
        collect_fee_mode = cfg.get("collect_fee_mode", -1)
        
        # Filters: TVL, Mode 1 (SOL fees), Bin Step, Base Fee
        if (tvl >= min_tvl and
            collect_fee_mode == 1 and
            bin_step >= min_bin_step and
            base_fee >= min_fee):
            eligible_pools.append(p)
            
    if not eligible_pools:
        return None
    
    # Select highest TVL pool
    eligible_pools.sort(key=lambda x: x.get("tvl", x.get("liquidity", 0)), reverse=True)
    return eligible_pools[0]

# ─── Main Bot Logic ───────────────────────────────────────────
def check_tokens(config, state):
    logging.info("Scanning GMGN rank for tokens...")
    query = build_auth_query(config)
    query["chain"] = "sol"
    query["interval"] = "24h"
    query["limit"] = "100"
    query["direction"] = "desc"
    query["orderby"] = "volume"
    
    headers = {
        "X-APIKEY": config["gmgn_api_key"],
        "Content-Type": "application/json"
    }
    
    url = f"{HOST}/v1/market/rank"
    try:
        res = requests.get(url, params=query, headers=headers, timeout=15)
        res.raise_for_status()
        data = res.json()
        if data.get("code") != 0:
            logging.error(f"GMGN API Error: {data}")
            return
            
        tokens = data.get("data", {}).get("rank", [])
        now = time.time()
        
        for t in tokens:
            address = t.get("address")
            symbol = t.get("symbol", "UNKNOWN")
            
            # Skip if already holding a position for this token
            if address in state["active_positions"]:
                continue
                
            # Apply Safety Filters
            renounced_mint = t.get("renounced_mint", 0)
            renounced_freeze = t.get("renounced_freeze_account", 0)
            if renounced_mint != 1 or renounced_freeze != 1:
                continue
                
            creation_timestamp = t.get("creation_timestamp", now)
            if now - creation_timestamp < 2 * 3600: # 2 hours
                continue
                
            market_cap = t.get("market_cap", 0)
            if market_cap < 250_000:
                continue
                
            volume = t.get("volume", 0)
            if volume < 1_000_000:
                continue
                
            gas_fee = t.get("gas_fee", 0)
            if gas_fee < 30:
                continue
                
            holder_count = t.get("holder_count", 0)
            if holder_count < 900:
                continue
                
            if t.get("top_10_holder_rate", 1.0) > 0.30:
                continue
            if t.get("dev_team_hold_rate", 1.0) > 0.05:
                continue
            if t.get("bundler_rate", 1.0) > 0.68:
                continue
            if t.get("entrapment_ratio", 1.0) > 0.30:
                continue
            if t.get("rug_ratio", 1.0) > 0.50:
                continue
                
            ath_mcap = t.get("history_highest_market_cap", 0)
            if ath_mcap == 0:
                continue
                
            # ATH proximity check: MC >= 80% of ATH
            if market_cap < ath_mcap * 0.80:
                continue
                
            # ─── Technical Analysis Checks ────────────────────────
            klines = get_kline_data(config, address)
            df = calculate_indicators(klines)
            if df is None:
                continue
                
            last_candle = df.iloc[-1]
            
            # Condition 1: Must be above Supertrend (dir == 1)
            if last_candle.get("ST_dir", -1) != 1:
                continue
                
            # Condition 2: Historical breakout & dump allowance
            # Find when the breakout occurred in last 50 candles
            breakout_idx = None
            # Find index where close broke past previous ATH
            # (Simplification: look for a candle that broke the high of previous candles)
            for idx in range(-min(50, len(df)), -1):
                c = df.iloc[idx]
                prev_max = df.iloc[:idx]['high'].max()
                if c['close'] > prev_max and c['volume'] > df.iloc[idx-10:idx]['volume'].mean() * 1.5:
                    breakout_idx = idx
                    break
                    
            if breakout_idx is None:
                # No high-volume ATH breakout found recently
                continue
                
            # Condition 3: Has not generated any exit signal since the breakout
            has_exit_signal_since_breakout = False
            for idx in range(breakout_idx, 0):
                is_exit, _ = check_exit_signal(df, idx)
                if is_exit:
                    has_exit_signal_since_breakout = True
                    break
                    
            if has_exit_signal_since_breakout:
                continue
                
            # ─── Meteora Pool Checks ──────────────────────────────
            pools = fetch_meteora_pools(address)
            selected_pool = select_best_pool(config, pools)
            if not selected_pool:
                continue
                
            pool_address = selected_pool["address"]
            
            # Check range eligibility (uninitialized bin arrays) via bridge
            range_check = run_bridge(["check-range", pool_address])
            if not range_check.get("success") or not range_check.get("eligible"):
                logging.info(f"Skipping pool {pool_address} for {symbol} due to uninitialized bin arrays or rent cost.")
                continue
                
            # ─── Open Position ────────────────────────────────────
            # Fetch balance
            bal_check = run_bridge(["get-balance"])
            if not bal_check.get("success"):
                logging.error(f"Failed to fetch balance: {bal_check.get('error')}")
                continue
                
            wallet_sol = bal_check.get("balance", 0.0)
            refundable_rent = range_check.get("refundable_rent", 0.25)
            gas_reserve = config.get("gas_reserve", 0.05)
            
            deposit_sol = wallet_sol - refundable_rent - gas_reserve
            if deposit_sol < 0.01:
                logging.warning(f"Aborting open: insufficient balance. Sol: {wallet_sol}, Rent: {refundableRent}, Reserve: {gas_reserve}")
                continue
                
            logging.info(f"🔔 OPEN SIGNAL for {symbol} ({address}) in pool {pool_address}. Deposit Amount: {deposit_sol:.4f} SOL")
            
            open_res = run_bridge(["open", pool_address, f"{deposit_sol:.6f}"])
            if open_res.get("success"):
                pos_addr = open_res.get("position")
                state["active_positions"][address] = {
                    "symbol": symbol,
                    "pool_address": pool_address,
                    "position_address": pos_addr,
                    "lower_bin": open_res.get("lower_bin"),
                    "upper_bin": open_res.get("upper_bin"),
                    "deposit_sol": deposit_sol,
                    "refundable_rent": open_res.get("refundable_rent"),
                    "opened_at": time.time()
                }
                save_state(state)
                
                # Send telegram message
                msg = (
                    f"✅ <b>POSITION OPENED: {symbol}</b>\n\n"
                    f"<b>Pool Address:</b> <code>{pool_address}</code>\n"
                    f"<b>Position:</b> <code>{pos_addr}</code>\n"
                    f"<b>Deposit:</b> {deposit_sol:.5f} SOL\n"
                    f"<b>Refundable Rent:</b> {open_res.get('refundable_rent'):.5f} SOL\n"
                    f"<b>Range:</b> {open_res.get('lower_bin')} to {open_res.get('upper_bin')}\n"
                    f"🔗 <a href='https://gmgn.ai/sol/token/{address}'>View on GMGN</a>"
                )
                send_telegram(config, msg)
                logging.info(f"Position successfully opened: {pos_addr}")
            else:
                logging.error(f"Failed to open position on-chain: {open_res.get('error')}")
                
    except Exception as e:
        logging.error(f"Error checking tokens: {e}")

# ─── Monitor Active Positions ─────────────────────────────────
def monitor_positions(config, state):
    if not state["active_positions"]:
        return
        
    logging.info(f"Monitoring {len(state['active_positions'])} active positions...")
    now = time.time()
    
    # Iterate copy of keys because we might remove items from dict
    for token_address in list(state["active_positions"].keys()):
        pos = state["active_positions"][token_address]
        symbol = pos["symbol"]
        pool_address = pos["pool_address"]
        position_address = pos["position_address"]
        
        # 1. Check if price is Upward Out-of-Range
        range_check = run_bridge(["check-range", pool_address])
        if range_check.get("success"):
            active_bin = range_check.get("active_bin")
            upper_bin = pos["upper_bin"]
            
            if active_bin is not None and active_bin > upper_bin:
                logging.info(f"🔔 REBALANCE: Price went Upward Out-of-Range for {symbol} (active_bin {active_bin} > upper_bin {upper_bin}). Closing and rebalancing...")
                
                # Close Position
                close_res = run_bridge(["close", pool_address, position_address])
                if close_res.get("success"):
                    token_x_amount = close_res.get("token_x_amount", 0.0)
                    token_x_mint = close_res.get("token_x_mint")
                    
                    # Swap token X back to SOL
                    swap_res = {"success": True}
                    if token_x_amount > 0:
                        logging.info(f"Swapping {token_x_amount} {symbol} back to SOL...")
                        swap_res = run_bridge(["swap", token_x_mint, "SOL", f"{token_x_amount:.8f}"])
                        
                    # Remove from active state
                    del state["active_positions"][token_address]
                    state["history"].append({
                        "token_address": token_address,
                        "symbol": symbol,
                        "pool_address": pool_address,
                        "position_address": position_address,
                        "deposit_sol": pos["deposit_sol"],
                        "closed_at": now,
                        "reason": "upward_out_of_range"
                    })
                    save_state(state)
                    
                    msg = (
                        f"🔄 <b>REBALANCE CLOSED: {symbol}</b>\n"
                        f"Price went above position upper bin. Position closed and swapped back to SOL.\n\n"
                        f"<b>Withdrawn Token Amount:</b> {token_x_amount:.4f} {symbol}\n"
                        f"<b>Swap Tx:</b> <code>{swap_res.get('tx', 'N/A')}</code>"
                    )
                    send_telegram(config, msg)
                    
                    # Try to re-open immediately with updated parameters
                    # By returning, the main loop will scan again and find it if criteria match
                    continue
                else:
                    logging.error(f"Failed to close position for rebalance: {close_res.get('error')}")
        
        # 2. Check for Exit Signal on 5m candles
        klines = get_kline_data(config, token_address)
        df = calculate_indicators(klines)
        if df is not None:
            is_exit, triggers = check_exit_signal(df)
            if is_exit:
                logging.info(f"🚨 EXIT SIGNAL for {symbol} ({token_address}). Triggers: {triggers}")
                
                # Close Position
                close_res = run_bridge(["close", pool_address, position_address])
                if close_res.get("success"):
                    token_x_amount = close_res.get("token_x_amount", 0.0)
                    token_x_mint = close_res.get("token_x_mint")
                    
                    # Swap token X back to SOL
                    swap_res = {"success": True}
                    if token_x_amount > 0:
                        logging.info(f"Swapping {token_x_amount} {symbol} back to SOL...")
                        swap_res = run_bridge(["swap", token_x_mint, "SOL", f"{token_x_amount:.8f}"])
                        
                    # Save to state history
                    del state["active_positions"][token_address]
                    state["history"].append({
                        "token_address": token_address,
                        "symbol": symbol,
                        "pool_address": pool_address,
                        "position_address": position_address,
                        "deposit_sol": pos["deposit_sol"],
                        "closed_at": now,
                        "reason": f"exit_signal: {', '.join(triggers)}"
                    })
                    save_state(state)
                    
                    msg = (
                        f"🚨 <b>EXIT SIGNAL TRIGGERED: {symbol}</b>\n"
                        f"Position has been liquidated and swapped back to SOL.\n\n"
                        f"<b>Triggers:</b> {', '.join(triggers)}\n"
                        f"<b>Swap Tx:</b> <code>{swap_res.get('tx', 'N/A')}</code>"
                    )
                    send_telegram(config, msg)
                else:
                    logging.error(f"Failed to liquidate position: {close_res.get('error')}")

# ─── Main Bot Loop ────────────────────────────────────────────
def main():
    logging.info("🤖 DLMM Trading Bot starting...")
    config = load_config()
    state = load_state()
    
    # Send start message
    send_telegram(config, "🤖 <b>Meteora DLMM Trading Bot Started</b>\nMonitoring GMGN filters and positions 24/7.")
    
    last_token_scan = 0
    last_monitor_tick = 0
    
    while True:
        now = time.time()
        
        # Scan for new tokens every 60 seconds
        if now - last_token_scan >= 60:
            try:
                config = load_config() # Reload config dynamically
                check_tokens(config, state)
            except Exception as e:
                logging.error(f"Error in token scan loop: {e}")
            last_token_scan = time.time()
            
        # Monitor active positions every 15 seconds
        if now - last_monitor_tick >= 15:
            try:
                monitor_positions(config, state)
            except Exception as e:
                logging.error(f"Error in position monitor loop: {e}")
            last_monitor_tick = time.time()
            
        time.sleep(5)

if __name__ == "__main__":
    main()
