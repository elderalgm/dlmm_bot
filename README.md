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

### 🛡️ A. Detaylı GMGN.ai Risk ve Güvenlik Filtreleri
Bot, Solana üzerindeki zararlı akıllı sözleşmelerden (scam, rugpull, bal dökme/honeypot) korunmak ve sadece gerçek hacimli/likiditeli projelere girmek için son derece agresif bir güvenlik filtresi zinciri çalıştırır. Her token tarama esnasında aşağıdaki kurallardan **birini bile ihlal ederse anında elenir**:

1.  **Mint Renounced (Basma Yetkisi İptali):**
    *   **Teknik Detay:** Token sözleşmesinin kurucusunda veya herhangi bir adreste yeni token basma (`Mint`) yetkisi bulunmamalıdır (`renounced_mint == 1`). 
    *   **Amaç:** Geliştiricinin sonradan trilyonlarca token basarak havuzdaki tüm SOL'leri çekmesini engellemek.
2.  **Freeze Authority Renounced (Hesap Dondurma İptali):**
    *   **Teknik Detay:** Geliştirici cüzdanın veya akıllı sözleşmenin bireysel cüzdanları dondurma yetkisi kaldırılmış olmalıdır (`renounced_freeze_account == 1`).
    *   **Amaç:** Kullanıcıların satın aldıkları tokenları satmalarının engellenmesini (karaliste/blacklisting) engellemek.
3.  **Minimum Token Yaşı (Token Age):**
    *   **Teknik Detay:** Token'ın piyasaya çıkış veya ilk likidite eklenme anından itibaren en az **2 saat (7200 saniye)** geçmiş olmalıdır.
    *   **Amaç:** İlk saniyelerdeki aşırı yüksek bot manipülasyonundan ve anlık "pump-and-dump" tuzaklarından uzak durmak.
4.  **Market Cap (Piyasa Değeri) Eşiği:**
    *   **Teknik Detay:** Token'ın anlık piyasa değeri (Market Cap) **en az $250.000** olmalıdır.
    *   **Amaç:** Çok düşük likiditeye sahip, küçük alımlarla bile fiyatı manipüle edilebilen aşırı mikro projeleri filtrelemek.
5.  **24 Saatlik Minimum Hacim:**
    *   **Teknik Detay:** Token'ın son 24 saatteki ticaret hacmi **en az $1.000.000** olmalıdır.
    *   **Amaç:** Meteora DLMM havuzunda yüksek miktarda komisyon (LP fee) üretebilmek için organik ticaret hacminin döndüğü havuzları hedeflemek.
6.  **Minör Gaz Ücreti Akışı (Gas Fee):**
    *   **Teknik Detay:** Token havuzlarında harcanan toplam gaz ücreti **en az 30 SOL** seviyesine ulaşmış olmalıdır.
    *   **Amaç:** Sahte hacim botları (Wash Trading) yerine gerçek Solana kullanıcılarının işlem yaptığı organik havuzları doğrulamak.
7.  **Dağıtık Cüzdan ve Holder Limiti (Holder Count):**
    *   **Teknik Detay:** Token'ı cüzdanında tutan benzersiz adres sayısı **en az 900** olmalıdır.
    *   **Amaç:** Token sahipliğinin birkaç balinada toplanmadığını, topluluğa yayıldığını doğrulamak.
8.  **Balina ve İlk 10 Cüzdan Sınırı (Top 10 Holders Rate):**
    *   **Teknik Detay:** En çok token tutan ilk 10 cüzdanın (likidite havuzları hariç) toplam arz içindeki payı **%30'dan küçük** olmalıdır (`top_10_holder_rate <= 0.30`).
    *   **Amaç:** İlk 10 cüzdandaki balinaların tek bir satışıyla fiyatın sıfırlanma (dump) riskini önlemek.
9.  **Geliştirici Takım Cüzdanı Sınırı (Dev Team Hold Rate):**
    *   **Teknik Detay:** Projeyi çıkaran geliştirici ekibin elinde tuttuğu toplam arz oranı **%5'ten küçük** olmalıdır (`dev_team_hold_rate <= 0.05`).
    *   **Amaç:** Geliştiricinin elindeki büyük payı yatırımcıların üzerine boşaltmasını (Dev dump) engellemek.
10. **Önceden Toplu Alım Oranı (Bundler Rate):**
    *   **Teknik Detay:** Geliştiricinin token lansmanı esnasında aynı anda birden fazla cüzdanla otomatik alım yaptığı oran **%68'den küçük** olmalıdır (`bundler_rate <= 0.68`).
    *   **Amaç:** Geliştiricinin arzın çoğunu gizlice kendi cüzdanlarına dağıtıp gizli tekel kurduğu projeleri engellemek.
11. **Tuzak ve Kara Kutu Oranı (Entrapment Ratio):**
    *   **Teknik Detay:** Token'ın genel akıllı sözleşme analizinde tespit edilen tuzak puanı **%30'dan küçük** olmalıdır (`entrapment_ratio <= 0.30`).
    *   **Amaç:** İçeriğinde gizli kod açıkları barındıran akıllı sözleşmeleri engellemek.
12. **Rugpull Analiz Oranı (Rug Ratio):**
    *   **Teknik Detay:** Sözleşme denetiminde rugpull ihtimali göstergesi **%50'den küçük** olmalıdır (`rug_ratio <= 0.50`).
    *   **Amaç:** Likiditenin aniden kilit açma yöntemiyle çalınabileceği projeleri elemek.

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
| `state_sync_interval_seconds` | `1800` | Cüzdan / zincir durum eşitleme sıklığı (saniye). VPS için 1800-3600 (30-60 dk) önerilir. |

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
