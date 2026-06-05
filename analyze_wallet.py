import sys
import os
import json
import requests
import time
from datetime import datetime

# Public RPC fallback
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"

def load_config():
    config_path = "config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def get_rpc_url(config):
    url = config.get("rpc_url")
    if not url or "YOUR_SOLANA_RPC_URL" in url:
        return DEFAULT_RPC
    return url

def rpc_call(url, method, params):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params
    }
    try:
        res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        if res.status_code == 200:
            return res.json().get("result")
    except Exception as e:
        print(f"RPC Error calling {method}: {e}")
    return None

def main():
    print("====================================================")
    print("         SOLANA CÜZDAN HAREKETLERİ ANALİZİ")
    print("====================================================")
    
    config = load_config()
    rpc_url = get_rpc_url(config)
    
    # Try to derive public key from private key if exists in environment or config
    wallet_address = ""
    private_key_b58 = os.environ.get("WALLET_PRIVATE_KEY") or config.get("wallet_private_key")
    
    if private_key_b58 and "YOUR_" not in private_key_b58:
        try:
            import base58
            from nacl.signing import SigningKey
            raw_key = base58.b58decode(private_key_b58)
            signing_key = SigningKey(raw_key[:32])
            wallet_address = base58.b58encode(signing_key.verify_key.encode()).decode('utf-8')
            print(f"Cüzdan adresi ayarlardan çözümlendi: {wallet_address}")
        except Exception:
            pass
            
    if not wallet_address:
        wallet_address = input("Lütfen analiz etmek istediğiniz Solana Cüzdan Adresini girin: ").strip()
        if not wallet_address:
            print("Hata: Geçersiz cüzdan adresi.")
            return

    print(f"\n[1/3] RPC bağlantısı kuruluyor: {rpc_url}")
    print(f"[2/3] Cüzdanın son 25 hareketi çekiliyor...")
    
    signatures_result = rpc_call(rpc_url, "getSignaturesForAddress", [wallet_address, {"limit": 25}])
    if not signatures_result:
        print("Hata: İşlem geçmişi çekilemedi. RPC adresi hatalı veya ağ yoğun olabilir.")
        return
        
    print(f"Toplam {len(signatures_result)} adet işlem bulundu. Detaylar analiz ediliyor...\n")
    print(f"{'Tarih / Saat':<19} | {'Net SOL Değişimi':<18} | {'İşlem Tipi / Detay':<30} | {'İşlem Kodu (Signature)':<12}")
    print("-" * 105)
    
    for sig_info in signatures_result:
        sig = sig_info.get("signature")
        block_time = sig_info.get("blockTime")
        dt_str = datetime.fromtimestamp(block_time).strftime('%Y-%m-%d %H:%M:%S') if block_time else "Bilinmiyor"
        
        # Fetch transaction details
        tx = rpc_call(rpc_url, "getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not tx:
            # Try again once
            tx = rpc_call(rpc_url, "getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            
        if not tx:
            print(f"{dt_str:<19} | {'Hata (Okunamadı)':<18} | {'Detay çekilemedi':<30} | {sig[:12]}...")
            continue
            
        meta = tx.get("meta", {})
        transaction = tx.get("transaction", {})
        message = transaction.get("message", {})
        
        # Find index of our wallet in the account keys
        account_keys = message.get("accountKeys", [])
        wallet_idx = -1
        
        for idx, acc in enumerate(account_keys):
            acc_address = acc if isinstance(acc, str) else acc.get("pubkey")
            if acc_address == wallet_address:
                wallet_idx = idx
                break
                
        if wallet_idx == -1:
            print(f"{dt_str:<19} | {'0.00000 SOL':<18} | {'Cüzdan dışı hareket':<30} | {sig[:12]}...")
            continue
            
        # Extract pre and post balances
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])
        
        if wallet_idx < len(pre_balances) and wallet_idx < len(post_balances):
            change_lamports = post_balances[wallet_idx] - pre_balances[wallet_idx]
            change_sol = change_lamports / 1e9
        else:
            change_sol = 0.0
            
        # Detect Programs Involved
        program_ids = []
        for acc in account_keys:
            pubkey = acc if isinstance(acc, str) else acc.get("pubkey")
            program_ids.append(pubkey)
            
        tx_type = "Bilinmeyen İşlem"
        
        METEORA_DLMM_PROG = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
        JUPITER_PROG = "JUP6LkbZbjS1jKKppHS29YxgwhdXXnuCwStCrmedfTR"
        
        is_meteora = METEORA_DLMM_PROG in program_ids
        is_jupiter = JUPITER_PROG in program_ids
        
        if is_meteora and is_jupiter:
            tx_type = "Meteora + Jupiter (Swap & Liquidity)"
        elif is_meteora:
            tx_type = "Meteora DLMM İşlemi"
            # If large negative change, probably open position rent
            if change_sol < -0.01:
                tx_type += " (Havuz Açılış / Kira)"
            elif change_sol > 0.01:
                tx_type += " (Havuz Kapanış / İade)"
        elif is_jupiter:
            tx_type = "Jupiter Swap (Token Alım/Satım)"
        else:
            # Check for system transfer or ATA creation
            if len(account_keys) > 2 and change_sol == 0.0:
                tx_type = "ATA Hesap Kurulumu (Rent)"
            elif change_sol < -0.00203:
                tx_type = "Transfer / Hesap Açılışı"
            else:
                tx_type = "Genel İşlem"
                
        # Format output
        sign_str = "+" if change_sol > 0 else ""
        color_sol = f"{sign_str}{change_sol:.5f} SOL"
        
        print(f"{dt_str:<19} | {color_sol:<18} | {tx_type:<30} | {sig}")
        
    print("\n====================================================")
    print("ANALİZ TAMAMLANDI.")
    print("Meteora DLMM Havuz Açılış işlemlerindeki eksi bakiyeler pozisyon kapandığında cüzdanınıza iade edilir.")
    print("Eğer havuz kapandığı halde bakiye eksik görünüyorsa, on-chain işlemlerinizi tarayıcıdan (Solscan vb.) doğrulayabilirsiniz.")
    print("====================================================")

if __name__ == "__main__":
    main()
