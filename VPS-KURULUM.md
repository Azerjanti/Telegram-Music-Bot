# VPS Kurulum Rehberi — Telegram Music Bot

Bu rehber, botu **Ubuntu 22.04 / 24.04** bir VPS üzerinde systemd servisi olarak
kalıcı şekilde çalıştırmayı anlatır. Bot **long polling** kullanır; yani Telegram'a
giden bir webhooksuz bağlantı açar — dışarıdan gelen hiçbir port açmanıza gerek yoktur.

## Gereksinimler

| Şey | Neden gerekli |
|---|---|
| Ubuntu 22.04/24.04 VPS, en az 1 GB RAM (2 GB önerilir) | Python 3.12+ hazır gelir |
| ~2 GB disk + müzik önbelleği için alan | Katalog (SQLite) + geçici MP3 dosyaları |
| `ffmpeg` | MP3 dönüşümü ve Shazam ses tanıma için **zorunlu** |
| `curl_cffi` (requirements'ta var) | VPS/datacenter IP'lerinde YouTube bot-duvarını aşmak için önemli |

## Adım 1 — Sunucuya bağlan ve sistem paketlerini kur

```bash
ssh root@VPS_IP

apt update && apt upgrade -y
apt install -y git python3 python3-venv python3-pip ffmpeg curl
```

Python sürümünü doğrulayın (3.12 veya üstü olmalı):

```bash
python3 --version
```

> Python 3.13 de çalışır; `requirements.txt` 3.13 için gereken `audioop-lts`
> paketini otomatik ekler.

## Adım 2 — Bot kullanıcısı aç (önerilir, root yerine)

```bash
adduser botuser
su - botuser
```

## Adım 3 — Kodu indir

```bash
git clone https://github.com/Azerjanti/Telegram-Music-Bot.git music-bot
cd music-bot
```

## Adım 4 — Sanal ortam (venv) ve bağımlılıklar

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

Bu kurulum `python-telegram-bot`, `yt-dlp`, `shazamio`, `curl_cffi`, `ffmpeg-python`
gibi her şeyi çözer. Hata verirse `ffmpeg` kurulumunu (Adım 1) kontrol edin.

## Adım 5 — Telegram bot tokenı al

1. Telegram'da **@BotFather**'ı açın.
2. `/newbot` → bot adı ve kullanıcı adı verin.
3. Size verilen `123456:ABC-DEF...` biçimindeki tokenı kopyalayın.

## Adım 6 — `.env` dosyasını oluştur

```bash
cp .env.example .env
nano .env
```

Doldurulması gereken **zorunlu** alan:

```dotenv
BOT_TOKEN=BabaFaderdenAlinanToken
```

VPS için **önemle önerilen** değişiklikler (kalıcı disk kullanımı):

```dotenv
# /tmp yerine kalıcı bir yol — yoksa her restart'ta katalog ve "Локальный топ" sıfırlanır
MUSIC_DB_PATH=/home/botuser/music-bot/data/music.sqlite3
AUDIO_CACHE_DIR=/home/botuser/music-bot/data/audio

# Bot sahibinin Telegram ID'si (henüz bilmiyorsanız boş bırakın, Adım 8'de öğreneceksiniz)
ADMIN_ID=
ADMIN_IDS=

# Shazam ile ses tanıma açıksa (ffmpeg kurulu olmalı)
ENABLE_SHAZAM=true
ENABLE_YTDLP_DOWNLOADS=true
```

Dosyayı kaydedin (`Ctrl+O`, `Enter`, `Ctrl+X`). `.env` zaten `.gitignore` içinde,
tokenınız git'e girmez.

## Adım 7 — İlk çalıştırma (elle test)

```bash
.venv/bin/python -m music_bot.main
```

- Telegram'da botunuza `/start` yazın, bir şarkı arayın.
- Logda `Using the local VPS catalogue at ...` satırını görün.
- Test bitince `Ctrl+C` ile durdurun.

## Adım 8 — Admin ID'ni öğren ve yaz

`.env`'de `ADMIN_ID` boşsa, botunuza `/id` (veya `/admin`) gönderin — bot size
sayısal ID'nizi cevaplar. Bu sayıyı `.env` içine yazın:

```dotenv
ADMIN_ID=123456789
```

Artık `/admin` paneli size açılır.

## Adım 9 — systemd servisi ile kalıcı hale getir

Root olarak (`su - root` veya `exit`):

```bash
nano /etc/systemd/system/music-bot.service
```

İçerik (yol ve kullanıcı adını kendinize göre düzenleyin):

```ini
[Unit]
Description=Telegram Music Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/music-bot
ExecStart=/home/botuser/music-bot/.venv/bin/python -m music_bot.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> Bot, `.env` dosyasını kendisi okur (önce çalışma dizini, sonra proje kökü);
> bu yüzden `EnvironmentFile` satırına gerek yoktur.

Servisi başlat:

```bash
systemctl daemon-reload
systemctl enable --now music-bot
systemctl status music-bot
```

## Adım 10 — Doğrulama

```bash
# Canlı loglar:
journalctl -u music-bot -f

# Health endpoint (PORT varsayılan 8000):
curl http://localhost:8000/health   # cevap: OK
```

- `/start`, şarkı arama, `Поиск по голосу` (ses tanıma), `/like` ve `/admin`
  panelini Telegram üzerinden deneyin.
- 8000 portunu dışarı açmanıza gerek yoktur; ufw kullanıyorsanız kapalı bırakın.

## Günlük işletme notları

| İş | Komut |
|---|---|
| Logları izle | `journalctl -u music-bot -f` |
| Botu yeniden başlat | `systemctl restart music-bot` |
| Botu durdur / başlat | `systemctl stop music-bot` / `start` |
| Güncelleme al | `cd /home/botuser/music-bot && git pull && .venv/bin/pip install -r requirements.txt && systemctl restart music-bot` |

## İsteğe bağlı: PostgreSQL

Bot varsayılan olarak yerel SQLite (`MUSIC_DB_PATH`) kullanır — çoğu kurulum için
yeterlidir ve katalog VPS diskinden asla kaybolmaz. PostgreSQL isterseniz:

```dotenv
DATABASE_URL=postgresql://kullanici:sifre@localhost:5432/musicbot
```

Veritabanı tabloları ilk bağlantıda otomatik oluşturulur. (Not: kod, Supabase
değişkenlerini bilinçli olarak yok sayar — katalog daima yerel VPS depolamasında tutulur.)

## Sorun giderme

- **`ffmpeg: command not found`** → `apt install -y ffmpeg`
- **YouTube indirmeleri "Sign in to confirm you're not a bot" diyor** →
  `curl_cffi` kurulu olmalı (requirements'ta var) ve `.env`'deki
  `YTDLP_*` ayarlarını değiştirmeyin; VPS IP'si için bu sertleştirme gereklidir.
- **Restart sonrası "Локальный топ" boşaldı** → `MUSIC_DB_PATH` hâlâ `/tmp`'i
  gösteriyor; kalıcı bir yola taşıyın.
- **`/admin` panel açılmıyor** → bot size ID'nizi yazıyordur; onu `ADMIN_ID`'ye girin.
- **Şarkı 50 MB üstü** → Telegram Bot API limiti (`MAX_TELEGRAM_FILE_MB=50`),
  bot API ile dosya gönderim sınırıdır.
