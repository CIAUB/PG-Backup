# 🛡️ PG-Backup

<div align="center">

### Professional Backup, Restore & Migration Utility for PasarGuard & PG-Node

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=20&pause=1000&color=8B0000&center=true&vCenter=true&width=500&lines=Sharingan-sharp+Backups;Silent+Migration%2C+Zero+Downtime;PostgreSQL+%26+Docker+Automation" alt="Typing SVG" />

![Version](https://img.shields.io/badge/Version-v3.2-8B0000)
![Python](https://img.shields.io/badge/Python-3.8+-8B0000)
![Platform](https://img.shields.io/badge/Platform-Linux-8B0000)
![Docker](https://img.shields.io/badge/Docker-Supported-8B0000)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supported-8B0000)
![Telegram](https://img.shields.io/badge/Telegram-Automation-8B0000)
![SSH](https://img.shields.io/badge/SSH-Migration-8B0000)
![License](https://img.shields.io/badge/License-MIT-8B0000)

Backup • Restore • Migration • Telegram Automation • PostgreSQL • Docker

</div>

---

<div dir="rtl">

# 🚀 معرفی

**PG-Backup** یک ابزار حرفه‌ای برای تهیه نسخه پشتیبان، ریستور و مهاجرت سرویس‌های **PasarGuard** و **PG-Node** است.

این ابزار با هدف ساده‌سازی مدیریت زیرساخت طراحی شده و امکان انتقال کامل سرویس‌ها بین سرورها، ارسال خودکار بکاپ به تلگرام و بازیابی سریع اطلاعات را فراهم می‌کند.

---

# ✨ قابلیت‌ها

* 📦 بکاپ از PasarGuard
* 📦 بکاپ کامل از PasarGuard + PG-Node
* 🔄 ریستور کامل از فایل ZIP
* 🚀 انتقال مستقیم به سرور جدید
* 🤖 ارسال خودکار بکاپ به تلگرام
* ⏰ بکاپ زمان‌بندی‌شده
* 🗄️ بکاپ و ریستور PostgreSQL
* 🐳 مدیریت خودکار Docker Stack
* ⚙️ نصب خودکار وابستگی‌ها
* 🔐 انتقال امن از طریق SSH

---

# 🛠 نصب

برای نصب PG-Backup کافی است دستور زیر را با دسترسی Root اجرا کنید:

```bash
sudo bash -c "$(curl -sL https://raw.githubusercontent.com/CIAUB/PG-Backup/main/install.sh)"
```

در صورتی که لینک اصلی در دسترس نبود:

```bash
sudo bash -c "$(curl -sL https://raw.githack.com/CIAUB/PG-Backup/main/install.sh)"
```

> 💡 لینک دوم از CDN جایگزین استفاده می‌کند و در برخی شبکه‌ها پایداری بیشتری دارد.

---

# 🚀 اجرا

پس از نصب، برای باز کردن منوی ابزار کافی است دستور زیر را اجرا کنید:

```bash
PG-Backup
```

---

# 🖥️ منوی اصلی

```text
1 ─ 🚀 Auto Backup & Transfer to New Server
2 ─ 🤖 Auto Backup to Telegram Bot (Scheduled)
3 ─ 💾 Manual Backup (Save Locally)
4 ─ 🔄 Manual Restore (From Local ZIP)
5 ─ 🚪 Exit
```

---

# 📦 حالت‌های بکاپ

### 1️⃣ PasarGuard Only

```text
/opt/pasarguard
/var/lib/pasarguard
PostgreSQL Database
```

---

### 2️⃣ PasarGuard + PG-Node

```text
/opt/pasarguard
/var/lib/pasarguard
PostgreSQL Database

/opt/pg-node
/var/lib/pg-node
```

---

# 🚀 انتقال به سرور جدید

این قابلیت برای مهاجرت کامل سرویس به سرور جدید طراحی شده است.

### فرآیند انتقال

```text
Create Backup
     │
     ▼
Send To Telegram (Optional)
     │
     ▼
Connect To New Server
     │
     ▼
Upload Backup
     │
     ▼
Restore Data
     │
     ▼
Start Services
```

### اطلاعات موردنیاز

```text
Server IP
Root Password
Telegram Bot Token (Optional)
Telegram Chat ID (Optional)
```

---

# 🤖 بکاپ خودکار تلگرام

امکان تهیه نسخه پشتیبان در بازه زمانی دلخواه و ارسال مستقیم به تلگرام.

نمونه بازه‌ها:

```text
30 Minutes
1 Hour
6 Hours
12 Hours
24 Hours
```

برای اجرای دائمی:

```bash
screen -S pg-backup pg-backup
```

یا:

```bash
nohup pg-backup > backup.log 2>&1 &
```

---

# 💾 بکاپ دستی

نمونه فایل خروجی:

```text
backup_pg_20260801120000.zip
```

یا:

```text
backup_full_20260801120000.zip
```

---

# 🔄 ریستور دستی

ریستور کامل از فایل ZIP شامل:

* فایل‌های برنامه
* تنظیمات
* دیتابیس PostgreSQL
* داده‌های PG-Node
* داده‌های PasarGuard

### مراحل ریستور

```text
Stop Containers
Restore Files
Restore Database
Start Containers
Health Check
```

---

# 📁 ساختار فایل بکاپ

```text
backup_full_YYYYMMDDHHMMSS.zip
│
├── docker-compose.yml
├── .env
│
├── pg_dump
│   ├── globals.sql
│   ├── db-001.sql
│   └── manifest.tsv
│
├── pasarguard_data
│
├── pg_node_opt
│
└── pg_node_data
```

---

# 📂 مسیرهای پیش‌فرض

| سرویس      | مسیر کانفیگ       | مسیر داده             |
| ---------- | ----------------- | --------------------- |
| PasarGuard | `/opt/pasarguard` | `/var/lib/pasarguard` |
| PG-Node    | `/opt/pg-node`    | `/var/lib/pg-node`    |

---

# ⚠️ نکات مهم

* اجرای ابزار نیازمند دسترسی Root است.
* هنگام ریستور سرویس‌ها به‌صورت موقت متوقف می‌شوند.
* اطلاعات ورود SSH ذخیره نمی‌شوند.
* فایل‌های بکاپ شامل اطلاعات حساس هستند.
* توصیه می‌شود نسخه‌های پشتیبان در محل امن نگهداری شوند.

---

<p align="center">
<img src="https://raw.githubusercontent.com/CIAUB/CIAUB/main/sharingan.jpg" width="500" alt="Sharingan" />
</p>

---

# 📞 ارتباط با توسعه‌دهنده

* 👨‍💻 Telegram: https://t.me/VCIAU
* 🐙 GitHub: https://github.com/CIAUB

---

### ❤️ حمایت از پروژه

اگر این پروژه برای شما مفید بوده است، با ثبت ⭐ در GitHub از توسعه آن حمایت کنید.

---

<sub><sub>Developed by CIAUB</sub></sub>

</div>
