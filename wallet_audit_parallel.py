import requests
import time
from datetime import datetime
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

addr = "4GPgaaCtP6yvxSKYkHjzWBgpyXEXTixW14Cej9LgQMtw"

# Pool of public RPC endpoints
endpoints = [
    "https://api.mainnet-beta.solana.com",
    "https://solana.publicnode.com",
    "https://rpc.ankr.com/solana"
]

def get_transaction_details(sig, index, total):
    # Rotate endpoints based on index
    node = endpoints[index % len(endpoints)]
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    }
    
    # Try different endpoints on failure
    for attempt in range(len(endpoints) * 2):
        current_node = endpoints[(index + attempt) % len(endpoints)]
        try:
            r = requests.post(current_node, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            if r.status_code == 200:
                res = r.json()
                if "result" in res and res["result"] is not None:
                    return res["result"]
            elif r.status_code == 429:
                time.sleep(1.0)
        except Exception:
            pass
        time.sleep(0.5)
    return None

def main():
    print("Fetching signatures...")
    # We use mainnet-beta to fetch signatures (it maintains the full index)
    sigs = []
    before = None
    node = "https://api.mainnet-beta.solana.com"
    while True:
        params = [addr, {"limit": 1000}]
        if before:
            params[1]["before"] = before
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": params
        }
        try:
            r = requests.post(node, json=payload, timeout=10)
            batch = r.json().get("result", [])
            if not batch:
                break
            sigs.extend(batch)
            if len(batch) < 1000:
                break
            before = batch[-1]["signature"]
        except Exception as e:
            print("Error fetching signatures:", e)
            break
        time.sleep(0.5)
        
    print(f"Total signatures fetched: {len(sigs)}")
    
    # Filter signatures since June 3rd (timestamp 1780454400)
    june_sigs = [s for s in sigs if s.get("blockTime", 0) >= 1780454400]
    print(f"Signatures since June 3rd: {len(june_sigs)}")
    june_sigs.reverse()  # Oldest to newest
    
    total_sigs = len(june_sigs)
    audit_log = []
    
    print(f"Fetching details for {total_sigs} transactions in parallel...")
    
    # Run in parallel using ThreadPoolExecutor
    max_workers = 12
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(get_transaction_details, s_info["signature"], idx, total_sigs): s_info
            for idx, s_info in enumerate(june_sigs)
        }
        
        completed_count = 0
        for future in as_completed(futures):
            s_info = futures[future]
            tx_res = future.result()
            completed_count += 1
            
            if completed_count % 50 == 0 or completed_count == total_sigs:
                print(f"Downloaded details for [{completed_count}/{total_sigs}] transactions")
                
            if not tx_res:
                continue
                
            meta = tx_res.get("meta", {})
            if not meta:
                continue
                
            sig = tx_res.get("transaction", {}).get("signatures", [None])[0]
            if not sig:
                continue
                
            block_time = tx_res.get("blockTime")
            dt = datetime.fromtimestamp(block_time)
            dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            
            # Find wallet index
            account_keys = tx_res.get("transaction", {}).get("message", {}).get("accountKeys", [])
            wallet_idx = -1
            for idx, acc in enumerate(account_keys):
                acc_addr = acc if isinstance(acc, str) else acc.get("pubkey")
                if acc_addr == addr:
                    wallet_idx = idx
                    break
                    
            if wallet_idx == -1:
                continue
                
            pre_bal = meta.get("preBalances", [])[wallet_idx] / 1e9
            post_bal = meta.get("postBalances", [])[wallet_idx] / 1e9
            delta = post_bal - pre_bal
            fee = meta.get("fee", 0) / 1e9
            
            program_ids = []
            for acc in account_keys:
                pubkey = acc if isinstance(acc, str) else acc.get("pubkey")
                program_ids.append(pubkey)
                
            METEORA_DLMM_PROG = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
            JUPITER_PROG = "JUP6LkbZbjS1jKKppHS29YxgwhdXXnuCwStCrmedfTR"
            
            tx_type = "Other"
            if METEORA_DLMM_PROG in program_ids and JUPITER_PROG in program_ids:
                tx_type = "Meteora + Jupiter Swap"
            elif METEORA_DLMM_PROG in program_ids:
                tx_type = "Meteora DLMM"
            elif JUPITER_PROG in program_ids:
                tx_type = "Jupiter Swap"
            elif len(account_keys) > 2 and delta == 0.0:
                tx_type = "ATA Creation / Setup"
            elif delta > 0.005 and len(account_keys) <= 4:
                tx_type = "SOL Transfer In (Deposit)"
            elif delta < -0.005 and len(account_keys) <= 4:
                tx_type = "SOL Transfer Out (Withdraw)"
                
            audit_log.append({
                "time": dt_str,
                "timestamp": block_time,
                "sig": sig,
                "pre": pre_bal,
                "post": post_bal,
                "delta": delta,
                "fee": fee,
                "type": tx_type
            })
            
    print("\nProcessing audit log...")
    # Sort by timestamp
    audit_log.sort(key=lambda x: x["timestamp"])
    
    # Find initial balance (on/after June 4th)
    initial_balance = None
    for tx in audit_log:
        if tx["timestamp"] >= 1780598400: # June 4th
            initial_balance = tx["pre"]
            break
            
    if audit_log:
        final_balance = audit_log[-1]["post"]
    else:
        final_balance = 0.0
        
    print("\n--- AUDIT SUMMARY ---")
    print(f"Initial balance (before June 4th): {initial_balance} SOL")
    print(f"Final balance (current): {final_balance} SOL")
    if initial_balance is not None and final_balance is not None:
        diff = final_balance - initial_balance
        print(f"Net SOL Difference: {diff:+.8f} SOL")
        
    # Analyze by transaction type
    type_totals = {}
    type_counts = {}
    total_fees = 0.0
    for tx in audit_log:
        t_type = tx["type"]
        type_totals[t_type] = type_totals.get(t_type, 0.0) + tx["delta"]
        type_counts[t_type] = type_counts.get(t_type, 0) + 1
        total_fees += tx["fee"]
        
    print("\nNet Delta by Transaction Type:")
    for t_type, total in sorted(type_totals.items()):
        count = type_counts[t_type]
        print(f"  {t_type:<25} ({count:<3} txs): {total:+.8f} SOL")
        
    print(f"\nTotal Tx Fees Paid: {total_fees:.8f} SOL")
    
    # Save log to JSON
    with open("full_wallet_audit.json", "w") as f:
        json.dump(audit_log, f, indent=2)
    print("\nSaved full log to full_wallet_audit.json")

if __name__ == "__main__":
    main()
