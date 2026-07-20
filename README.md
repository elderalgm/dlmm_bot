# 🤖 Solana Meteora DLMM Trading Bot

Solana blokzinciri üzerinde **Meteora DLMM (Dynamic Liquidity Market Maker)** havuzlarında otomatik likidite sağlayan (LP), **GMGN.ai** ve **Jupiter Swap API V2** entegrasyonuna sahip, Python ve Node.js tabanlı hibrit otomasyon ticaret botudur.

---

## 📌 1. Botun Amacı ve Çalışma Prensibi

Bu bot, Solana ekosistemindeki yeni ve yüksek hacimli tokenları tespit ederek **Meteora DLMM** havuzlarında otomatik olarak fiyat aralığı (Range) belirler, likidite ekler ve işlem ücreti (Fee) toplar. Fiyat hedef dışına çıktığında veya teknik göstergeler (RSI, EMA, Bollinger Bantları) sat sinyali verdiğinde pozisyonu otomatik kapatıp bakiyeyi SOL'e dönüştürür.

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  GMGN.ai Rank   │ ──> │ Güvenlik ve Risk │ ──> │ Meteora DLMM SDK │
│ Scanner (Solana)│     │   Filtreleri     │     │ Pozisyon Açılışı │
└─────────────────┘     └──────────────────┘     └──────────────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ Jupiter Swap V2 │ <── │  Teknik Çıkış    │ <── │ 7/24 Pozisyon    │
│ (Token ──> SOL) │     │ Sinyali (RSI/EMA)│     │ Takip Döngüsü    │
└─────────────────┘     └──────────────────┘     └──────────────────┘
```

---

## 🚀 2. Öne Çıkan Özellikler ve Güvenlik Filtreleri

### 🛡️ A. GMGN.ai Risk ve Güvenlik Filtreleri
Bot, piyasadaki dolandırıcılık (Rugpull, Honeypot) ve manipülasyon risklerini en aza indirmek için her tokenı şu filtrelerden geçirir:
* **Mint & Freeze Renounced:** Token basma ve dondurma yetkilerinin devredilmiş olması zorunludur.
* **Minimum Token Yaşı:** En az 2 saattir piyasada olan tokenlar taranır.
* **Market Cap & Hacim:** Minimum **$250.000 Market Cap** ve **$1.000.000 24s Hacim** şartı aranır.
* **Holder & Dağılım:** En az 900 holder; Top 10 Holder oranı `< %30`, Geliştirici Cüzdanı `< %5`, Bundler Oranı `< %68`, Entrapment Oranı `< %30`, Rug Oranı `< %50`.
* **ATH Yakınlığı:** Fiyatın son ATH seviyesinin en az %80 yakınında olması gereklidir.

### 🚫 B. Honeypot ve Vergi (Tax) Engeli
* **Buy/Sell Tax & Honeypot:** `buy_tax`, `sell_tax` veya `is_honeypot` verisi içeren vergili meme coinler otomatik elenir. Slippage ve vergi kayıpları engellenir.

### 💎 C. Sıfır Kira Kaybı (Uninitialized Bin Array Protection)
* Meteora DLMM havuzlarında ilk defa açılan fiyat kutuları (Bin Arrays) geri ödemesiz Solana kira ücreti (`~0.07 SOL/bin`) harcar.
* Bot, pozisyon açmadan önce on-chain denetim yaparak **sadece tüm kutuları önceden başlatılmış (0 uninitialized bin array)** güvenli havuzlara giriş yapar.

### ☁️ D. Bulut Tabanlı Durum Yedekleme (kvdb.io State Backup)
* Sunucu yeniden başlasa veya çöксе bile local `state.json` dosyasını `kvdb.io` bulut veritabanından 1 saniyede otomatik geri yükler. Aktif pozisyonlar ve işlem geçmişi asla kaybolmaz.

### ⛽ E. Düşük Öncelikli Gaz Ücreti (Priority Fee Optimization)
* Sabit yüksek gaz ücretleri yerine yapılandırılabilir öncelik ücreti (`priority_fee_micro_lamports`) kullanarak ağ işlem maliyetlerini 15 kat azaltır.

---

## 🏗️ 3. Proje Mimarisi

Proje, performans için **Node.js (Solana Web3 & DLMM SDK)** ile karar mekanizması için **Python** dilini birleştiren modüler bir yapıya sahiptir:

* 🐍 [dlmm_bot.py](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/dlmm_bot.py): Ana ticaret döngüsü, GMGN tarayıcısı, indikatör analizi, Telegram bot komutları ve karar mekanizması.
* ⚡ [meteora_bridge.js](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/meteora_bridge.js): Solana blokzincir işlemleri, Meteora DLMM SDK aralığı hesabı, Bin Array denetimi ve Jupiter Swap V2 motoru.
* 🌐 [server.py](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/server.py): Webhook ve sağlık kontrolü (Health Check) HTTP sunucusu.
* 📊 [wallet_audit_parallel.py](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/wallet_audit_parallel.py): Cüzdan işlemlerini paralel blokzincir işçileriyle tarayan denetim scripti.

---

## ⚙️ 4. Yapılandırma (`config.json`) Rehberi

`config.json` dosyasındaki parametrelerin açıklamaları aşağıdadır:

| Parametre | Varsayılan Değer | Açıklama |
| :--- | :--- | :--- |
| `rpc_url` | `YOUR_SOLANA_RPC_URL` | Helius veya QuickNode Solana Mainnet RPC adresi. |
| `wallet_private_key` | `YOUR_PRIVATE_KEY` | Base58 formatında cüzdan özel anahtarı. |
| `telegram_token` | `YOUR_BOT_TOKEN` | Telegram BotFather'dan alınan bot token'ı. |
| `chat_id` | `YOUR_CHAT_ID` | Bildirimlerin gönderileceği Telegram Chat ID. |
| `gmgn_api_key` | `gmgn_bd244...` | GMGN OpenAPI anahtarı. |
| `jupiter_api_key` | `jup_73f07...` | Jupiter Swap API V2 geliştirici anahtarı. |
| `dry_run` | `false` | `true` ise gerçek işlem açmaz, simülasyon yapar. |
| `gas_reserve` | `0.05` | Cüzdanda gaz ücretleri için bırakılacak minimum SOL. |
| `min_tvl` | `15000` | Havuz için gerekli minimum TVL ($). |
| `min_bin_step` | `80` | Minimum Bin Step değeri. |
| `min_base_fee_pct` | `2.0` | Havuzun minimum temel komisyon oranı (%). |
| `priority_fee_micro_lamports` | `100000` | İşlem başına ödenecek öncelik ücreti (micro-lamports). |

---

## 📦 5. Kurulum ve Çalıştırma

### 🌐 A. VPS Üzerinde Kurulum (Önerilen - 7/24 Kesintisiz)

1. **Gerekli Paketleri Yükleyin (Ubuntu/Debian):**
   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install -y python3 python3-pip python3-venv git curl
   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
   sudo apt install -y nodejs
   sudo npm install -g pm2
   ```

2. **Projeyi Klonlayın ve Bağımlılıkları Yükleyin:**
   ```bash
   git clone https://github.com/elderalgm/dlmm_bot.git
   cd dlmm_bot
   npm install
   pip3 install -r requirements.txt
   ```

3. **Yapılandırmayı Oluşturun:**
   ```bash
   cp config.example.json config.json
   nano config.json
   ```
   *(Cüzdan anahtarınızı ve Telegram API bilgilerinizi girip kaydedin)*

4. **Botu PM2 ile 7/24 Başlatın:**
   ```bash
   pm2 start dlmm_bot.py --name "dlmm-bot" --interpreter python3
   pm2 save
   pm2 startup
   ```

---

### 💻 B. Yerel Ortamda (Local Windows/Linux) Çalıştırma

```bash
# Bağımlılıkları yükleyin
npm install
pip install -r requirements.txt

# Ayar dosyanızı oluşturun
cp config.example.json config.json

# Botu çalıştırın
python dlmm_bot.py
```

---

## 📱 6. Telegram Bot Komutları

Telegram botunuz üzerinden sol alttaki **Menü** butonunu kullanarak veya direkt yazarak aşağıdaki komutları kullanabilirsiniz:

* 📊 `/status` - Cüzdan bakiyesi, aktif pozisyonlar ve Helius RPC kullanım raporu.
* 🔍 `/candidates` - Anlık GMGN tarama sonuçları ve elenen/uygun token listesi.
* 🚨 `/close <mint>` - İlgili pozisyonu derhal on-chain kapatır ve SOL'e çevirir.
* ⏸️ `/pause` - Yeni pozisyon aramayı duraklatır (aktif pozisyonlar takip edilmeye devam eder).
* ▶️ `/resume` - Botu tekrar aktif eder.
* ❓ `/help` - Komut yardım menüsü.

---

## ⚠️ 7. Sorumluluk Reddi (Disclaimer)

* Bu yazılım eğitim ve otomasyon amaçlı geliştirilmiştir. 
* Kripto para piyasaları ve DLMM havuzları yüksek risk içerir.
* Özel anahtarınızı (`wallet_private_key`) asla kimseyle paylaşmayın ve `config.json` dosyanızı git depolarına yüklemeyin.
