# 🤖 Solana Meteora DLMM Trading Bot - Detaylı Teknik Kılavuz ve Çalışma Prensipleri

Bu doküman, botun tüm mimari bileşenlerini, havuz seçimi kriterlerini, poza giriş-takip-çıkış döngüsünü ve teknik indikatör kurallarını en ince ayrıntısına kadar açıklamaktadır.

---

## 📌 1. Mimari ve Stratejik Amaç

Botun temel amacı, Solana ağındaki yüksek hacimli ve yeni trend olan meme coinleri tarayarak **Meteora DLMM (Dynamic Liquidity Market Maker)** havuzlarında otomatik fiyat aralıkları (LP) oluşturup işlem ücreti (Fee) toplamaktır. 

Stratejinin çekirdeği **"Tek Yönlü SOL (Single-Sided SOL) Likiditesi"** eklemektir. Bot, aktif fiyatın altına (fiyat düşüş yönüne doğru) likidite yerleştirir. Fiyat yükseldikçe pozisyondaki tokenlar tamamen SOL'e dönüşür, toplanan fee'ler alınır ve pozisyon karla kapatılır.

---

## 🔍 2. Detaylı Havuz Seçimi Kriterleri (Pool Selection)

Bot, GMGN.ai üzerinden güvenlik testlerini geçen bir token için Meteora DLMM API'sini sorgulayarak tüm havuzları çeker ve en uygun olanını seçmek için şu filtreleri uygular:

1. **TVL (Toplam Kilitli Değer) Eşiği:** Havuzun anlık TVL değeri `config.json` dosyasındaki `min_tvl` (Varsayılan: **$15.000**) değerinden büyük veya eşit olmalıdır. Küçük havuzlar likidite yetersizliği nedeniyle elenir.
2. **Bin Step (Fiyat Adım Genişliği):** Havuzun Bin Step değeri `config.get("min_bin_step", 80)` değerinden büyük veya eşit olmalıdır. Düşük bin step değerine sahip havuzlar elenir.
3. **Temel Komisyon Oranı (Base Fee):** Havuzun ürettiği temel ticaret komisyon oranı `config.get("min_base_fee_pct", 2.0)` (%2.0) değerinden büyük veya eşit olmalıdır. Düşük fee üreten havuzlar karlı olmadığı için elenir.
4. **Sıralama Algoritması:** Filtreleri geçen havuzlar kendi içinde puanlanır. TVL kriterini sağlayan havuzlar önceliklendirilir ve en yüksek TVL'e sahip olan havuz pozisyon açılmak üzere seçilir.

---

## 📥 3. Pozisyona Giriş Metodu (Position Entry)

Seçilen havuzda pozisyon açılışı, [meteora_bridge.js](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/meteora_bridge.js#L140) içindeki `cmdOpen` fonksiyonu ile tamamen on-chain olarak yürütülür:

1. **Aralık Hesaplama (Downside Range):** Aktif bin fiyatı baz alınarak **-%91** oranında aşağı yönlü fiyat sınırı hesaplanır.
   $$\text{Lower Price} = \text{Active Price} \times (1 - \frac{91}{100})$$
   Bu fiyat adımı, havuzun `binStep` parametresi kullanılarak DLMM SDK'sının `getBinIdFromPrice` fonksiyonu ile `lowerBinId` değerine çevrilir.
2. **Kira Kontrolü ve Engelleme (Bin Array Check):** `lowerBinId` ile `activeBinId` aralığındaki tüm fiyat kutularının (bin arrays) Solana üzerinde önceden başlatılıp başlatılmadığı sorgulanır. Eğer başlatılmamış (uninitialized) en ufak bir kutu varsa, **Solana ağına geri iadesiz kira harcaması yapmamak için** işlem anında iptal edilir (`missingCount == 0` zorunluluğu).
3. **Kira Muafiyeti Hesaplama (Rent Exemption):** Açılacak pozisyon hesabının kaplayacağı alan için gereken Solana kira miktarı `getPositionRentExemption` SDK fonksiyonu ile dinamik olarak sorgulanır. Bu bakiye (`~0.15 - 0.25 SOL`), pozisyon kapatıldığında cüzdana tam olarak iade edilir.
4. **Dinamik Likidite Stratejisi (Spot vs. Bid-Ask):** 
   * Havuz hem Token hem SOL kazandırıyorsa (`collect_fee_mode == 0`) **VE** komisyon oranı `%5`'ten küçükse (`base_fee_pct < 5.0`): Pozisyon **`StrategyType.BidAsk`** ile açılır. Likidite aktif fiyata yakın yoğunlaşır.
   * Diğer durumlarda Pozisyon **`StrategyType.Spot`** ile açılır. Likidite fiyat aralığına eşit olarak dağıtılır.
5. **Ağ İşlem Gönderimi:** İşlem öncelikli ücreti (`priority_fee_micro_lamports`) eklenerek imzalanır ve ağa iletilir.

---

## 👁️ 4. Pozisyon Takip ve Analiz Döngüsü (Monitoring)

Bot, aktif pozisyonları her **30 saniyede bir** [dlmm_bot.py](file:///c:/Users/ASUS/.gemini/antigravity-ide/scratch/dlmm_bot/dlmm_bot.py#L996) içindeki `monitor_positions` fonksiyonu ile on-chain ve off-chain olarak denetler:

### A. Fiyat Menzil Dışı (Rebalance) Çıkış Sinyali
* On-chain sorgu ile havuzun anlık aktif bin değeri (`active_bin`) çekilir.
* Eğer `active_bin > upper_bin` ise, token fiyatı pozisyon aralığımızın tamamen üstüne çıkmış demektir. Bu durumda pozisyondaki tüm varlıklar **%100 SOL'e dönüşmüştür**.
* Bot bunu anında tespit eder ve rebalance yapmak üzere pozisyonu kapatma komutu tetikler.

### B. 5 Dakikalık Grafik Teknik İndikatör Çıkış Sinyalleri
Token'ın 5 dakikalık grafikteki son 100 kline (mum) verisi GMGN API'den çekilir ve teknik göstergeler hesaplanır:
1. **RSI(2) Aşırı Alım:** RSI(2) değeri **90 ve üzerine** çıktığında aşırı alım bölgesi olarak tetiklenir.
2. **MACD Momentum Flipped:** MACD histogramı sıfırın altından sıfırın üstüne çıktığında (Kırmızıdan Yeşile ilk geçiş) tetiklenir.
3. **Bollinger Band Kırılımı:** Mumun en yüksek fiyatı veya kapanış fiyatı, üst Bollinger Bandı'nı (`BB_upper = SMA20 + 2 * STD20`) aştığında tetiklenir.
4. **Supertrend Flipped:** Supertrend indikatörünün yönü düşüşten (Kırmızı) yükselişe (Yeşil) döndüğünde tetiklenir.

> [!IMPORTANT]
> **Çıkış Kararı:** Yukarıdaki 4 teknik indikatör sinyalinden **en az 2 tanesi aynı anda tetiklenirse**, bot bunu güçlü bir "Kar Al ve Çık" sinyali kabul ederek pozisyonu derhal kapatır.

---

## 📤 5. Pozisyon Kapatma ve Tasfiye Metodu (Position Exit)

Pozisyon kapatma süreci iki aşamalı bir kurtarma ve temizlik zincirinden oluşur:

### Aşama 1: Pozisyonu On-Chain Kapatma (`cmdClose`)
1. **Fee Toplama:** Pozisyonda biriken tüm işlem komisyonları (X ve Y token cinsinden) claim edilir.
2. **Likidite Çekme:** Havuzda kalan tüm likidite çekilerek cüzdana aktarılır.
3. **Hesabı Kapatma:** Açılışta Solana ağına kilitlenen **Rent (Kira) Bakiyesi**, pozisyon hesabı tamamen kapatılarak cüzdana iade edilir (`getPositionRentExemption` iadesi).

### Aşama 2: Artık Tokenları SOL'e Dönüştürme (`cmdSwap`)
1. **Jupiter Swap Entegrasyonu:** Pozisyon kapandıktan sonra elimizde kalan tüm meme tokenlar (Token X), Jupiter Swap V2 API üzerinden anında piyasa fiyatıyla SOL'e satılır.
2. **Kalan Tozları Temizleme (Dust Sweeper):** Ağ gecikmeleri nedeniyle satılamayan veya cüzdanda kalan küçük miktardaki (dust) tokenlar, her 10 dakikada bir çalışan `swap-remaining-tokens` arka plan temizlik döngüsü ile otomatik olarak SOL'e dönüştürülür.

---

## 🛡️ 6. Detaylı Güvenlik ve Risk Filtreleri (Safety Filters)

Botun GMGNopenapi üzerinden yaptığı detaylı güvenlik testleri şunlardır:

* **renounced_mint == 1:** Akıllı sözleşme sahibinin sınırsız token basma yetkisi iptal edilmiş olmalıdır.
* **renounced_freeze_account == 1:** Kurucunun yatırımcıların hesaplarını dondurma yetkisi olmamalıdır.
* **Token Yaşı >= 2 Saat:** En az 2 saattir piyasada işlem görüyor olmalıdır.
* **Market Cap >= $250.000 ve 24s Hacim >= $1.000.000:** Projenin likidite derinliği ve organik hacmi olmalıdır.
* **Gas Fee >= 30 SOL:** Hacmin yapay botlar yerine gerçek kullanıcılar tarafından üretildiğini kanıtlar.
* **Holder Count >= 900:** Arzın geniş bir kitleye dağıldığını doğrular.
* **Top 10 Holder Oranı <= %30:** İlk 10 balinanın satış baskısını önler.
* **Dev Team Hold Oranı <= %5:** Ekibin elindeki tokenları yatırımcıya satmasını engeller.
* **Bundler Oranı <= %68:** Çıkış anında ekip tarafından gizlice yapılan toplu alımları engeller.
* **Rug Ratio <= %50 & Entrapment Ratio <= %30:** Sözleşme kodundaki gizli açıkları engeller.
* **Buy/Sell Tax == 0 & is_honeypot == 0:** Gizli alım satım vergileri olan ve satışı engellenen honeypot tokenları tamamen eler.

---

## ⚙️ 7. Yapılandırma (`config.json`) Parametre Rehberi

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `rpc_url` | `YOUR_SOLANA_RPC_URL` | Helius / QuickNode RPC Mainnet Adresi |
| `wallet_private_key` | `YOUR_PRIVATE_KEY` | Base58 Formatında Cüzdan Özel Anahtarı |
| `telegram_token` | `YOUR_BOT_TOKEN` | Telegram Bot API Anahtarı |
| `chat_id` | `YOUR_CHAT_ID` | Telegram Kullanıcı / Grup ID |
| `gmgn_api_key` | `gmgn_bd244...` | GMGNopenapi Erişim Anahtarı |
| `jupiter_api_key` | `jup_73f07...` | Jupiter API Erişim Anahtarı |
| `dry_run` | `false` | `true` ise simülasyon modunda çalışır |
| `gas_reserve` | `0.05` | Gaz ücretleri için cüzdanda bırakılacak SOL miktarı |
| `min_tvl` | `15000` | Hedef havuzun içermesi gereken minimum TVL ($) |
| `min_bin_step` | `80` | Hedef havuzun içermesi gereken minimum Bin Step |
| `min_base_fee_pct` | `2.0` | Hedef havuzun minimum temel komisyon oranı (%) |
| `priority_fee_micro_lamports` | `100000` | Öncelikli işlem ücreti. Ağ yoğunluğuna göre ayarlanır |
| `state_sync_interval_seconds` | `1800` | VPS için optimize edilmiş durum eşitleme sıklığı (saniye) |

---

## 🚀 8. VPS Kurulumu ve pm2 ile 7/24 Kesintisiz Çalıştırma

### Adım 1: VPS Sunucu Kurulumu (Ubuntu)
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt install -y nodejs
sudo npm install -g pm2
```

### Adım 2: Projeyi Klonlama ve Bağımlılıklar
```bash
git clone https://github.com/elderalgm/dlmm_bot.git
cd dlmm_bot
npm install
pip3 install -r requirements.txt
```

### Adım 3: Config Ayarı
```bash
cp config.example.json config.json
nano config.json
```
*(Özel anahtarınızı ve API bilgilerinizi girip kaydedin)*

### Adım 4: pm2 ile Arka Planda Başlatma
Projede hazır bulunan `ecosystem.config.js` yapılandırma dosyası sayesinde botu tek komutla (bellek limiti ve otomatik başlama optimizasyonları ile birlikte) başlatabilirsiniz:
```bash
pm2 start
pm2 save
pm2 startup
```
