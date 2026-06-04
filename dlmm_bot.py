import os
import sys
import time
import json
import uuid
import logging
import subprocess
import requests
import pandas as pd

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

def is_dry_run(config, state):
    override = state.get("dry_run_override")
    if override is not None:
        return override
    return config.get("dry_run", True)

def is_paused(state):
    return state.get("is_paused", False)

def is_rpc_limit_exceeded(state):
    credits_info = state.get("credits_tracked", {})
    used = credits_info.get("used", 0)
    # 900,000 credit limit (leaves a 100,000 buffer out of 1,000,000 free plan credits)
    if used >= 900000:
        return True
    return False

# Bridge Invoker
def run_bridge(cmd_args):
    # Track Helius RPC credit usage
    try:
        cost = 5
        if cmd_args:
            cmd = cmd_args[0]
            if cmd == "check-range":
                cost = 10
            elif cmd in ["open", "close"]:
                cost = 30
            elif cmd == "swap":
                cost = 20
                
        state = load_state()
        current_month = time.strftime("%Y-%m")
        if "credits_tracked" not in state:
            state["credits_tracked"] = {"month": current_month, "used": 0}
        if state["credits_tracked"].get("month") != current_month:
            state["credits_tracked"] = {"month": current_month, "used": 0}
        state["credits_tracked"]["used"] += cost
        save_state(state)
    except Exception as e:
        logging.error(f"Failed to track credit usage: {e}")
        
    # Determine dry_run value dynamically and inject into environment
    env = os.environ.copy()
    try:
        config = load_config()
        state = load_state()
        dry_run_val = is_dry_run(config, state)
        env["DRY_RUN"] = "true" if dry_run_val else "false"
    except Exception as e:
        logging.error(f"Failed to determine dry_run for bridge env: {e}")

    try:
        res = subprocess.run(
            ["node", "meteora_bridge.js"] + cmd_args,
            capture_output=True,
            text=True,
            timeout=45,
            env=env
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
def send_telegram(config, text, reply_markup=None):
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
    if reply_markup:
        payload["reply_markup"] = reply_markup
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
        
    # Calculate RSI(2) using Wilder's EMA (RMA) logic
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/2, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/2, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['RSI_2'] = 100 - (100 / (1 + rs))
    
    # Calculate MACD(12, 26, 9) histogram
    ema_fast = df['close'].ewm(span=12, adjust=False).mean()
    ema_slow = df['close'].ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    df['MACD_hist'] = macd_line - macd_signal
    
    # Calculate Bollinger Bands(20, 2) upper band
    sma20 = df['close'].rolling(window=20).mean()
    std20 = df['close'].rolling(window=20).std()
    df['BB_upper'] = sma20 + (2 * std20)
    df['BB_upper'] = df['BB_upper'].fillna(float('inf'))
    
    # Calculate Supertrend(10, 3)
    # Calculate TR (True Range)
    prev_close = df['close'].shift(1)
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Wilder's ATR
    atr = tr.ewm(alpha=1/10, adjust=False).mean()
    
    hl2 = (df['high'] + df['low']) / 2
    basic_upper = hl2 + 3 * atr
    basic_lower = hl2 - 3 * atr
    
    # Initialize Supertrend series
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    trend = pd.Series(1, index=df.index)
    
    for i in range(1, len(df)):
        if pd.isna(atr.iloc[i]):
            continue
            
        prev_upper = final_upper.iloc[i-1]
        prev_lower = final_lower.iloc[i-1]
        prev_close_val = df['close'].iloc[i-1]
        prev_trend = trend.iloc[i-1]
        
        # Upper Band
        if basic_upper.iloc[i] < prev_upper or prev_close_val > prev_upper:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = prev_upper
            
        # Lower Band
        if basic_lower.iloc[i] > prev_lower or prev_close_val < prev_lower:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = prev_lower
            
        # Trend direction
        if df['close'].iloc[i] > final_upper.iloc[i]:
            trend.iloc[i] = 1
        elif df['close'].iloc[i] < final_lower.iloc[i]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = prev_trend
            
    st_val = pd.Series(0.0, index=df.index)
    for i in range(len(df)):
        if trend.iloc[i] == 1:
            st_val.iloc[i] = final_lower.iloc[i]
        else:
            st_val.iloc[i] = final_upper.iloc[i]
            
    df['ST_dir'] = trend
    df['ST_val'] = st_val
    
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
    if is_paused(state):
        logging.info("Bot is paused. Skipping token scanning.")
        return
        
    if is_rpc_limit_exceeded(state):
        logging.warning("Helius RPC monthly credit limit reached! Stopping new token scans.")
        return

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

# ─── Telegram Command Handler ─────────────────────────────────
TELEGRAM_UPDATE_OFFSET = 0

def handle_telegram_candidates(config, state):
    send_telegram(config, "🔍 <b>GMGN aday tokenleri taranıyor...</b>\nBu işlem birkaç saniye sürebilir.")
    
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
            send_telegram(config, f"❌ GMGN API Hatası: {data.get('msg', 'Bilinmeyen Hata')}")
            return
            
        tokens = data.get("data", {}).get("rank", [])
        now = time.time()
        candidates = []
        
        for t in tokens:
            address = t.get("address")
            symbol = t.get("symbol", "UNKNOWN")
            
            # Skip if already holding a position
            if address in state.get("active_positions", {}):
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
            
            # If it passes safety, it's a candidate! Let's check indicators
            reason = ""
            if market_cap < ath_mcap * 0.80:
                reason = "❌ ATH Yakınlığı Yetersiz"
            else:
                klines = get_kline_data(config, address)
                df = calculate_indicators(klines)
                if df is None:
                    reason = "❌ Kline Alınamadı"
                else:
                    last_candle = df.iloc[-1]
                    if last_candle.get("ST_dir", -1) != 1:
                        reason = "❌ Supertrend Kırmızı"
                    else:
                        breakout_idx = None
                        for idx in range(-min(50, len(df)), -1):
                            c = df.iloc[idx]
                            prev_max = df.iloc[:idx]['high'].max()
                            if c['close'] > prev_max and c['volume'] > df.iloc[idx-10:idx]['volume'].mean() * 1.5:
                                breakout_idx = idx
                                break
                        if breakout_idx is None:
                            reason = "❌ ATH Kırılımı Yok"
                        else:
                            has_exit = False
                            for idx in range(breakout_idx, 0):
                                is_exit, _ = check_exit_signal(df, idx)
                                if is_exit:
                                    has_exit = True
                                    break
                            if has_exit:
                                reason = "❌ Kırılımdan Sonra Çıkış Sinyali"
                            else:
                                pools = fetch_meteora_pools(address)
                                selected_pool = select_best_pool(config, pools)
                                if not selected_pool:
                                    reason = "❌ Uygun Havuz Yok"
                                else:
                                    reason = "🟢 İşleme Giriş UYGUN!"
            
            candidates.append({
                "symbol": symbol,
                "address": address,
                "mcap": market_cap,
                "volume": volume,
                "reason": reason
            })
            
            if len(candidates) >= 10:
                break
                
        if not candidates:
            send_telegram(config, "🔍 <b>Aday Token Bulunamadı:</b> Güvenlik filtrelerini geçen hiçbir token yok.")
            return
            
        msg_lines = ["🔍 <b>GÜVENLİK FİLTRELERİNİ GEÇEN ADAYLAR</b>\n━━━━━━━━━━━━━━━━━━"]
        for idx, c in enumerate(candidates, 1):
            line = (
                f"{idx}. <b>{c['symbol']}</b> | MC: ${c['mcap']:,.0f} | Vol: ${c['volume']:,.0f}\n"
                f"  Durum: <code>{c['reason']}</code>\n"
                f"  🔗 <a href='https://gmgn.ai/sol/token/{c['address']}'>GMGN Linki</a>\n"
            )
            msg_lines.append(line)
            
        send_telegram(config, "\n".join(msg_lines))
        
    except Exception as e:
        logging.error(f"Error fetching candidates: {e}")
        send_telegram(config, f"❌ Hata: Aday listesi alınırken bir sorun oluştu: {str(e)}")

def check_telegram_commands(config, state):
    global TELEGRAM_UPDATE_OFFSET
    token = config.get("telegram_token")
    chat_id_expected = str(config.get("chat_id"))
    
    if not token or token.startswith("YOUR_"):
        return
        
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"offset": TELEGRAM_UPDATE_OFFSET + 1, "timeout": 2}
    
    try:
        res = requests.get(url, params=params, timeout=5)
        if res.status_code != 200:
            return
        data = res.json()
        if not data.get("ok"):
            return
            
        for update in data.get("result", []):
            TELEGRAM_UPDATE_OFFSET = update["update_id"]
            
            # Handle Inline Keyboard Callback Queries
            if "callback_query" in update:
                cb = update["callback_query"]
                cb_data = cb.get("data", "")
                cb_id = cb.get("id")
                chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                
                if chat_id != chat_id_expected:
                    continue
                    
                # Answer callback query to stop loading spinner
                try:
                    requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", json={"callback_query_id": cb_id})
                except Exception:
                    pass
                    
                if cb_data.startswith("close_"):
                    token_address = cb_data.split("_", 1)[1]
                    handle_telegram_close_position(config, state, token_address)
                continue
                
            message = update.get("message", {})
            text = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))
            
            if chat_id != chat_id_expected:
                continue
                
            # Parse text command
            cmd = text.lower()
            if cmd in ["/start", "start", "menu", "help"]:
                send_telegram_menu(config, state)
            elif cmd in ["/status", "📊 status"]:
                handle_telegram_status(config, state)
            elif cmd in ["/positions", "📈 active positions"]:
                handle_telegram_positions(config, state)
            elif cmd in ["/candidates", "🔍 aday tokenler"]:
                handle_telegram_candidates(config, state)
            elif cmd in ["/history", "📋 history"]:
                handle_telegram_history(config, state)
            elif cmd in ["/toggle_mode", "🔄 mod değiştir", "🟢 canlı moda geç", "🧪 simülasyona geç"]:
                current_val = is_dry_run(config, state)
                state["dry_run_override"] = not current_val
                save_state(state)
                new_mode = "🧪 DRY RUN (Simülasyon)" if state["dry_run_override"] else "🟢 LIVE TRADING (Gerçek)"
                send_telegram(config, f"🔄 <b>Mod Değiştirildi:</b> Çalışma modu artık <b>{new_mode}</b>.")
                send_telegram_menu(config, state)
            elif cmd in ["/toggle_pause", "⏸ durdur / başlat", "⏸ botu durdur", "▶️ botu başlat"]:
                current_val = is_paused(state)
                state["is_paused"] = not current_val
                save_state(state)
                status_str = "⏸ BOT DURDURULDU" if state["is_paused"] else "▶️ BOT BAŞLATILDI"
                send_telegram(config, f"📢 <b>{status_str}</b>\nBot artık işlemlerini askıya aldı." if state["is_paused"] else f"📢 <b>{status_str}</b>\nBot normal işlemlerine devam ediyor.")
                send_telegram_menu(config, state)
            elif cmd.startswith("/close_"):
                # Handle /close_tokenaddress direct text command
                token_address = text.split("_", 1)[1]
                handle_telegram_close_position(config, state, token_address)
                
    except Exception as e:
        logging.error(f"Error processing telegram commands: {e}")

def send_telegram_menu(config, state):
    is_p = is_paused(state)
    pause_btn_text = "▶️ Botu Başlat" if is_p else "⏸ Botu Durdur"
    
    dry_r = is_dry_run(config, state)
    mode_btn_text = "🟢 Canlı Moda Geç" if dry_r else "🧪 Simülasyona Geç"
    
    menu_keyboard = {
        "keyboard": [
            [{"text": "📊 Status"}, {"text": "📈 Active Positions"}],
            [{"text": "🔍 Aday Tokenler"}, {"text": "📋 History"}],
            [{"text": mode_btn_text}, {"text": pause_btn_text}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }
    
    mode_str = "🧪 DRY RUN (Simülasyon)" if dry_r else "🟢 LIVE TRADING (Gerçek)"
    status_str = "⏸ Durduruldu" if is_p else "▶️ Aktif (Çalışıyor)"
    
    msg = (
        "🤖 <b>Meteora DLMM Standalone Bot Menüsü</b>\n\n"
        f"<b>Durum:</b> {status_str}\n"
        f"<b>Mod:</b> {mode_str}\n\n"
        "Aşağıdaki butonları kullanarak botu yönetebilirsiniz:\n"
        "• <b>📊 Status</b>: Durum raporu ve bakiye.\n"
        "• <b>📈 Active Positions</b>: Açık pozisyonlar ve manuel kapatma.\n"
        "• <b>🔍 Aday Tokenler</b>: GMGN listesinden filtreleri geçen adaylar.\n"
        f"• <b>{mode_btn_text}</b>: Bot çalışma modunu değiştirir.\n"
        f"• <b>{pause_btn_text}</b>: Bot işlemlerini geçici olarak durdurur/başlatır.\n"
        "• <b>📋 History</b>: Son 5 kapatılan işlem."
    )
    send_telegram(config, msg, reply_markup=menu_keyboard)

def handle_telegram_status(config, state):
    # Fetch balance
    bal_res = run_bridge(["get-balance"])
    balance_str = f"{bal_res.get('balance', 0.0):.4f} SOL" if bal_res.get("success") else f"Hata ({bal_res.get('error', 'Bilinmeyen Hata')})"

    
    dry_run = is_dry_run(config, state)
    mode_str = "🧪 DRY RUN (Simülasyon)" if dry_run else "🟢 LIVE TRADING (Gerçek)"
    
    is_p = is_paused(state)
    status_str = "⏸ Durduruldu" if is_p else "▶️ Aktif (Çalışıyor)"
    
    # RPC Credits
    credits_info = state.get("credits_tracked", {})
    used_credits = credits_info.get("used", 0)
    limit = 900000
    credits_status = "🔴 Limit Aşımı" if used_credits >= limit else "🟢 Normal"
    
    msg = (
        "📊 <b>BOT DURUM RAPORU</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<b>Bot Durumu:</b> {status_str}\n"
        f"<b>Çalışma Modu:</b> {mode_str}\n"
        f"<b>Cüzdan Bakiyesi:</b> <code>{balance_str}</code>\n"
        f"<b>Helius RPC Kullanımı:</b> {used_credits:,} / {limit:,} ({credits_status})\n"
        f"<b>Aktif Pozisyon Sayısı:</b> {len(state.get('active_positions', {}))}\n"
        f"<b>Gaz Rezervi:</b> {config.get('gas_reserve', 0.05)} SOL\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<b>Tarama Kriterleri:</b>\n"
        f"• Min TVL: ${config.get('min_tvl', 15000):,}\n"
        f"• Min Bin Step: {config.get('min_bin_step', 80)}\n"
        f"• Min Base Fee: %{config.get('min_base_fee_pct', 2.0)}"
    )
    send_telegram(config, msg)

def handle_telegram_positions(config, state):
    positions = state.get("active_positions", {})
    if not positions:
        send_telegram(config, "📂 <b>Aktif pozisyon bulunmamaktadır.</b>")
        return
        
    for addr, pos in positions.items():
        symbol = pos.get("symbol", "UNKNOWN")
        pool = pos.get("pool_address", "UNKNOWN")
        pos_addr = pos.get("position_address", "UNKNOWN")
        deposit = pos.get("deposit_sol", 0.0)
        
        # Check range dynamically
        range_res = run_bridge(["check-range", pool])
        if range_res.get("success"):
            active_bin = range_res.get("active_bin")
            upper_bin = pos.get("upper_bin")
            lower_bin = pos.get("lower_bin")
            
            # Check range status
            if active_bin is not None and active_bin > upper_bin:
                status_str = "🔴 Out-of-Range (Yukarı)"
            elif active_bin is not None and active_bin < lower_bin:
                status_str = "🔴 Out-of-Range (Aşağı)"
            else:
                status_str = "🟢 In-Range (Aktif)"
                
            active_price = range_res.get("active_price", 0.0)
            price_details = f"Anlık Fiyat: {active_price:.6f} SOL/Lamport"
        else:
            status_str = "⚠️ Menzil Bilgisi Alınamadı"
            price_details = "Meteora on-chain verisi çekilemedi."
            active_bin = "N/A"
            lower_bin = pos.get("lower_bin", "N/A")
            upper_bin = pos.get("upper_bin", "N/A")
            
        msg = (
            f"📈 <b>POZİSYON: {symbol}</b>\n"
            f"<code>{addr}</code>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"<b>Yatırılan Tutar:</b> {deposit:.4f} SOL\n"
            f"<b>Menzil Durumu:</b> {status_str}\n"
            f"<b>Alt Bin:</b> {lower_bin} | <b>Üst Bin:</b> {upper_bin}\n"
            f"<b>Aktif Bin:</b> {active_bin}\n"
            f"<b>Detay:</b> {price_details}\n"
            "━━━━━━━━━━━━━━━━━━"
        )
        
        # Add Inline keyboard button to manually close the position
        inline_keyboard = {
            "inline_keyboard": [
                [{"text": "🚨 Pozisyonu Kapat & SOL'e Çevir", "callback_data": f"close_{addr}"}]
            ]
        }
        send_telegram(config, msg, reply_markup=inline_keyboard)

def handle_telegram_history(config, state):
    history = state.get("history", [])
    if not history:
        send_telegram(config, "📂 <b>İşlem geçmişi bulunmamaktadır.</b>")
        return
        
    msg_lines = ["📋 <b>SON 5 İŞLEM GEÇMİŞİ</b>\n━━━━━━━━━━━━━━━━━━"]
    # Show last 5 closed positions
    for pos in reversed(history[-5:]):
        symbol = pos.get("symbol", "UNKNOWN")
        deposit = pos.get("deposit_sol", 0.0)
        reason = pos.get("reason", "N/A")
        
        # Human readable date
        closed_at_ts = pos.get("closed_at", 0)
        closed_at = time.strftime('%H:%M:%S', time.localtime(closed_at_ts)) if closed_at_ts else "N/A"
        
        line = (
            f"• <b>{symbol}</b> | Giriş: {deposit:.3f} SOL\n"
            f"  Zaman: {closed_at} | Sebep: <code>{reason}</code>\n"
        )
        msg_lines.append(line)
        
    send_telegram(config, "\n".join(msg_lines))

def handle_telegram_close_position(config, state, token_address):
    positions = state.get("active_positions", {})
    if token_address not in positions:
        send_telegram(config, f"❌ Hata: <code>{token_address}</code> adresine ait açık bir pozisyon bulunamadı.")
        return
        
    pos = positions[token_address]
    symbol = pos.get("symbol", "UNKNOWN")
    pool = pos.get("pool_address", "UNKNOWN")
    pos_addr = pos.get("position_address", "UNKNOWN")
    
    send_telegram(config, f"⏳ <b>{symbol}</b> pozisyonu on-chain kapatılıyor ve swap işlemi başlatılıyor...")
    
    # Execute close position on-chain
    close_res = run_bridge(["close", pool, pos_addr])
    if close_res.get("success") or close_res.get("dry_run"):
        token_x_amount = close_res.get("token_x_amount", 0.0)
        token_x_mint = close_res.get("token_x_mint")
        
        swap_res = {"success": True, "tx": "N/A"}
        if token_x_amount > 0 and token_x_mint:
            # Swap token X back to SOL
            swap_res = run_bridge(["swap", token_x_mint, "SOL", f"{token_x_amount:.8f}"])
            
        # Update state
        del state["active_positions"][token_address]
        state["history"].append({
            "token_address": token_address,
            "symbol": symbol,
            "pool_address": pool,
            "position_address": pos_addr,
            "deposit_sol": pos.get("deposit_sol", 0.0),
            "closed_at": time.time(),
            "reason": "manual_telegram_request"
        })
        save_state(state)
        
        msg = (
            f"🚨 <b>POZİSYON MANUEL KAPATILDI: {symbol}</b>\n"
            f"Pozisyon başarıyla geri çekildi ve SOL swap işlemi tamamlandı.\n\n"
            f"<b>Çekilen Tutar:</b> {token_x_amount:.4f} {symbol}\n"
            f"<b>Swap Tx:</b> <code>{swap_res.get('tx', 'N/A')}</code>"
        )
        send_telegram(config, msg)
    else:
        error_msg = close_res.get("error", "Unknown error")
        send_telegram(config, f"❌ Hata: Pozisyon kapatılamadı: <code>{error_msg}</code>")

# ─── Startup Position Audit ───────────────────────────────────
def startup_position_audit(config, state):
    positions = state.get("active_positions", {})
    if not positions:
        logging.info("Startup Audit: No active positions found in state.")
        return
        
    logging.info(f"Startup Audit: Auditing {len(positions)} active positions...")
    send_telegram(config, "🔄 <b>Bot Başlatıldı:</b> Açık pozisyonlar ve çıkış sinyalleri denetleniyor...")
    
    now = time.time()
    for token_address in list(positions.keys()):
        pos = positions[token_address]
        symbol = pos.get("symbol", "UNKNOWN")
        pool_address = pos.get("pool_address")
        position_address = pos.get("position_address")
        
        if not pool_address or not position_address:
            continue
            
        logging.info(f"Startup Audit: Checking indicators for {symbol} ({token_address})...")
        klines = get_kline_data(config, token_address)
        df = calculate_indicators(klines)
        
        if df is not None:
            is_exit, triggers = check_exit_signal(df)
            if is_exit:
                logging.info(f"🚨 Startup Audit: EXIT SIGNAL found on startup for {symbol}! Triggers: {triggers}. Liquidating immediately...")
                send_telegram(config, f"🚨 <b>Başlangıç Denetimi:</b> {symbol} için 5m çıkış sinyali tespit edildi! Acil tasfiye ediliyor...")
                
                # Close Position
                close_res = run_bridge(["close", pool_address, position_address])
                if close_res.get("success") or close_res.get("dry_run"):
                    token_x_amount = close_res.get("token_x_amount", 0.0)
                    token_x_mint = close_res.get("token_x_mint")
                    
                    # Swap token X back to SOL
                    swap_res = {"success": True, "tx": "N/A"}
                    if token_x_amount > 0 and token_x_mint:
                        logging.info(f"Swapping {token_x_amount} {symbol} back to SOL...")
                        swap_res = run_bridge(["swap", token_x_mint, "SOL", f"{token_x_amount:.8f}"])
                        
                    # Save to state history
                    del state["active_positions"][token_address]
                    state["history"].append({
                        "token_address": token_address,
                        "symbol": symbol,
                        "pool_address": pool_address,
                        "position_address": position_address,
                        "deposit_sol": pos.get("deposit_sol", 0.0),
                        "closed_at": now,
                        "reason": f"startup_audit_exit: {', '.join(triggers)}"
                    })
                    save_state(state)
                    
                    msg = (
                        f"🚨 <b>BAŞLANGIÇTA ACİL TASFİYE TAMAMLANDI: {symbol}</b>\n"
                        f"Bot açılışında çıkış sinyali tespit edilerek pozisyon kapatıldı.\n\n"
                        f"<b>Tetikleyiciler:</b> {', '.join(triggers)}\n"
                        f"<b>Swap Tx:</b> <code>{swap_res.get('tx', 'N/A')}</code>"
                    )
                    send_telegram(config, msg)
                else:
                    err = close_res.get("error", "Unknown error")
                    logging.error(f"Failed to liquidate {symbol} on startup: {err}")
                    send_telegram(config, f"❌ Hata: {symbol} başlangıçta kapatılamadı: <code>{err}</code>")
            else:
                logging.info(f"Startup Audit: {symbol} is safe. No exit signal on startup.")
                send_telegram(config, f"🟢 <b>Başlangıç Denetimi:</b> {symbol} güvenli (çıkış sinyali yok).")
        else:
            logging.warning(f"Startup Audit: Could not fetch indicators for {symbol}.")

# ─── Main Bot Loop ────────────────────────────────────────────
def main():
    logging.info("🤖 DLMM Trading Bot starting...")
    config = load_config()
    state = load_state()
    
    # Send start message
    send_telegram_menu(config, state)
    
    # Run startup position audit before starting main loop
    try:
        startup_position_audit(config, state)
    except Exception as e:
        logging.error(f"Error in startup position audit: {e}")
    
    last_token_scan = 0
    last_monitor_tick = 0
    last_telegram_tick = 0
    
    while True:
        now = time.time()
        
        # Check Telegram commands every 5 seconds
        if now - last_telegram_tick >= 5:
            try:
                config = load_config() # Reload config dynamically
                check_telegram_commands(config, state)
            except Exception as e:
                logging.error(f"Error in telegram commands loop: {e}")
            last_telegram_tick = time.time()
        
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
