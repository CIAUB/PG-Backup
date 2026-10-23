# 📜 Changelog — PG-Backup

<div dir="rtl">

## v5.0.0 — security hardening pass (pre-release review)
- install.sh rewritten: release-tag source, SHA-256 verification (`pg_backup.py.sha256`, optional `--sha256`), private temp dir, atomic install, syntax check, no cache-buster/predictable /tmp path.
- In-app updater no longer offers to run an unverified installer.
- Panel: JSON body cap, session revocation on logout, encrypted TOTP secret, constant-time username compare, security headers + no-store, Telegram/proxy validation.
- Panel: schedulers made by the terminal menu / pre-4.2 versions / screen / tmux are now listed (the panel built the wrong unit name `pasarguard-backup-pasarguard-backup-1`), with a “Convert to service” action.
- NEW database-engine conversion (menu 7, `--cli convert`, panel page): TimescaleDB ⇄ PostgreSQL and MariaDB ⇄ MySQL. Reads a backup zip (or a fresh read-only backup of this server) and writes a NEW converted zip; never restores and never touches the running server. Cross-family conversion (SQLite/PostgreSQL/MySQL) is deliberately not offered.
- Panel: fully responsive (phones 320px+ and desktop): drawer menu with scrim, tables become cards, dialogs become bottom sheets, 44px touch targets, no iOS zoom, safe-area insets, Esc closes dialogs. Version/build shown in the sidebar.
- Panel freshness: updating PG-Backup now restarts a running web panel automatically, and the panel's JS/CSS are revalidated (ETag) instead of cached for an hour — the old UI could keep showing after an update.
- Panel: optional “skip Telegram test” when creating a scheduler (Telegram may be unreachable from the server).
- Panel: create/delete Telegram backup schedulers from the web UI (was only possible from the terminal menu).
- DB passwords are passed to docker via environment, not argv. Optional SSH host-key pinning. Caddy/systemd hardening, email/domain validation.

## v5.0.0 — پنل تحت وب + تبدیل نوع دیتابیس

* **پنل تحت وب (اختیاری، منوی 6):** سرویس جدا (Starlette + uvicorn) با رابط فارسی RTL، پشت Caddy با HTTPS خودکار (Let's Encrypt برای دامنه، یا گواهی داخلی برای IP) و مسیر مخفی تصادفی. ورود با رمز scrypt، TOTP + کد بازیابی، قفل موقت پس از تلاش ناموفق، نشست امضاشده و محافظت CSRF. محدودیت IP عمداً وجود ندارد. مدیریت بکاپ‌ها، زمان‌بندها (با لاگ زنده)، تنظیمات تلگرام (+ تست ارسال)، چندسرور و انتقال، و لاگ زنده‌ی کارها.
* **تبدیل نوع دیتابیس (منوی 7 و `--cli convert`):** TimescaleDB ⇄ PostgreSQL و MariaDB ⇄ MySQL. از یک فایل بکاپ (یا بکاپ تازه‌ی همین سرور) یک فایل بکاپ **جدید** می‌سازد؛ هیچ ریستوری انجام نمی‌شود و سرور فعلی دست نمی‌خورد.
* **CLI بدون پرسش:** `--cli backup | restore | transfer | telegram-send | convert`؛ اعتبارنامه‌ها فقط از متغیر محیطی/فایل 0600، نه آرگومان خط فرمان.
* بازآرایی: منطق انتقال و ریستور به توابع قابل‌استفاده‌ی مجدد (`_transfer_to_server`، `_restore_from_zip`) تقسیم شد؛ انتقال با **کلید SSH** هم پشتیبانی می‌شود.
* اجزای اضافی (پکیج `pgb`) هنگام نیاز دانلود می‌شوند و فایل‌به‌فایل با `pgb-manifest.json` — که SHA-256 آن داخل همین اسکریپت جاسازی شده — راستی‌آزمایی می‌شوند. `install.sh` تغییری نکرده است.
* منوی اصلی: «Update» به گزینه‌ی **8** (درست بالای خروج) رفت؛ وب‌پنل **6** و تبدیل دیتابیس **7** است.
* **رابط چندزبانه** (English پیش‌فرض / Русский / فارسی با RTL)، **تم سیستم‌محور** (تیره/روشن + انتخاب دستی) و فونت‌های بسته‌بندی‌شده‌ی Inter و Vazirmatn (OFL).

## v4.2.9 — رفع مسیر نسبی db_dump در Manual Restore محلی

* `workflow_manual_restore` هنگام ریستور دیتابیس، مسیر نسبی `"db_dump"` را به `_restore_databases_local` پاس می‌داد (هم برای SQLite و هم برای Postgres/TimescaleDB/MySQL/MariaDB). این مسیر توسط `_read_manifest`/`_diagnose_missing_manifest` مستقیماً با `os.path` باز می‌شد، پس نسبت به **دایرکتوری فعلی اجرای اسکریپت** resolve می‌شد، نه `/opt/pasarguard`. نتیجه: هر اجرای Manual Restore از مسیری غیر از خودِ `/opt/pasarguard` (رایج‌ترین حالت، چون فایل ZIP معمولاً جای دیگری است) با خطای کاذب «manifest.tsv not found or empty — db_dump does not exist locally» متوقف می‌شد، حتی وقتی استخراج آرشیو کاملاً موفق بود و `/opt/pasarguard/db_dump` پر از داده‌ی درست بود.
* هر دو محل فراخوانی اکنون `os.path.join(PASARGUARD_DIR, "db_dump")` پاس می‌دهند، یعنی مسیر همیشه مطلق است — مستقل از دایرکتوری اجرای اسکریپت. این فیکس هر پنج بک‌اند را پوشش می‌دهد چون `_restore_databases_local` نقطه‌ی مشترک ورودی برای همه‌شان است.
* مسیر Auto Transfer (SSH) از قبل درست بود، چون `_read_manifest_remote` صریحاً `PASARGUARD_DIR` را روی سرور ریموت پیش از هر دستور `cd` می‌کرد.

## v4.2.8 — یکسان‌سازی احراز هویت PostgreSQL/TimescaleDB

* `_backup_postgres_local` اکنون credential ها را از طریق `_resolve_pg_credentials()` (اول `.env`، سپس فال‌بک به `docker-compose.yml`) می‌خواند و `PGPASSWORD` را به `pg_dumpall`/`pg_dump` پاس می‌دهد — دقیقاً هماهنگ با چیزی که ریستور قبلاً انجام می‌داد. قبلاً بکاپ فقط از `backend["user"]` بدون هیچ رمزی استفاده می‌کرد، پس در هر نصبی که auth با رمز را اجباری کرده بود، یا `POSTGRES_USER`/`POSTGRES_PASSWORD` فقط در `docker-compose.yml` بود (نه `.env`)، بکاپ می‌توانست fail شود یا کاربر را اشتباه تشخیص دهد؛ حتی وقتی ریستور همان نصب درست کار می‌کرد.
* `_list_databases_local`/`_ssh` و `_pg_db_timescale_info` هم اکنون `PGPASSWORD` را forward می‌کنند تا شمارش دیتابیس‌ها و تشخیص اکستنشن TimescaleDB روی نصب‌های password-enforced بی‌صدا به حدس تک‌دیتابیسِ قدیمی سقوط نکند.
* `_resolve_pg_credentials()` اکنون ابتدا نام سرویس compose که واقعاً تشخیص داده شده را امتحان می‌کند، پیش از افتادن روی فهرست حدسیِ ثابت (`postgres`/`timescaledb`/`db`/`database`) — نصب‌هایی که سرویس Postgres/TimescaleDB در `docker-compose.yml`شان نام دیگری دارد (مثل `postgresql`، `pg`، `timescale` یا یک نام دلخواه) قبلاً credential هایی که همان‌جا موجود بود را بی‌صدا از دست می‌دادند.

## v4.2.7 — پاس رمز در enumeration دیتابیس‌های Postgres

* پاس‌ورد resolve‌شده اکنون به‌صورت `PGPASSWORD` در enumeration دیتابیس‌ها هم forward می‌شود؛ بدون آن، هر سرویس Postgres/TimescaleDB که auth با رمز را اجبار می‌کرد (پیش‌فرض ایمیج‌های رسمی postgres/timescaledb) این فراخوانی `psql` را رد می‌کرد و enumeration حتی با رمز درست، بی‌صدا به حدس تک‌دیتابیسِ قدیمی سقوط می‌کرد.

## v4.2.6 — رفع تشخیص کاربر سفارشی Postgres در enumeration

* `_list_databases_local` اکنون کاربر واقعیِ resolve‌شده را می‌پذیرد به‌جای هاردکد کردن `pasarguard`. در هر نصبی با کاربر سفارشی Postgres (از طریق `SQLALCHEMY_DATABASE_URL` یا `DB_USER` در `.env`)، auth قدیمی با `-U pasarguard` بی‌صدا fail می‌شد، به نام دیتابیس تک و قدیمیِ `pasarguard` سقوط می‌کرد، و `pg_dump` سعی می‌کرد دیتابیسی را دامپ کند که اغلب اصلاً وجود نداشت — دقیقاً برای همان نصب‌هایی که بیشترین نیاز به بکاپ چند-دیتابیسی را دارند.

## v4.2.5 — کشف credential از docker-compose.yml برای Postgres و MySQL

* توابع کمکی مشترک credential-resolution برای Postgres و MySQL اضافه شدند که هر دو قرارداد نام‌گذاری اسکریپت (`DB_USER`/`DB_PASSWORD`، `PG_USER`/`PG_PASSWORD`) و قرارداد ایمیج رسمی (`POSTGRES_USER`/`POSTGRES_PASSWORD`، `MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_ROOT_PASSWORD`) را می‌پذیرند.
* اگر `.env` چیزی نداشته باشد، اکنون خروجی `docker compose config` پارس می‌شود تا بلاک `environment:` سرویس دیتابیس استخراج شود (هم استایل `KEY: value` و هم `- KEY=value`) — برای نصب‌هایی که credential ها مستقیماً در `docker-compose.yml` هاردکد شده‌اند، نه در `.env`.
* این منطق در ریستور MySQL/MariaDB روی سرور ریموت هم به‌کار گرفته شد تا فهرست کاندیدهای user/password کامل‌تر باشد.

## v4.2.4 — رفع ریشه‌ای «manifest.tsv not found» در مهاجرت

* بکاپ تازه‌ساز به‌صورت **محلی** بلافاصله بعد از ساخته‌شدن اعتبارسنجی می‌شود (وجود `db_dump/manifest.tsv` + حداقل یک ردیف دیتا) — هم در Auto Transfer و هم در Manual Restore، **پیش از** توقف کانتینرها یا پاک‌شدن هر دایرکتوری. قبلاً این خطا فقط بعد از آپلود کامل + پاک‌شدن سرور مقصد + ری‌استارت کانتینرها کشف می‌شد.
* خواندن manifest از سرور ریموت اکنون یک بار با تأخیر کوتاه retry می‌شود.
* در صورت نبود واقعی manifest، خروجی تشخیصی شامل `find -maxdepth 2` کل دایرکتوری Pasarguard است تا مشخص شود extract کجا رفته.

## v4.2.3 — رفع `1045 Access denied` در ریستور MySQL/MariaDB

* پیش از start شدن MySQL روی سرور مقصد، data directory آن (bind mount **یا** named volume — با پارس `docker-compose.yml`) تشخیص داده و پاک می‌شود؛ چون MySQL فقط در init اول رمز `MYSQL_ROOT_PASSWORD` را از env می‌خواند و دیتای قدیمی روی bind mount با `docker compose down -v` پاک نمی‌شود.
* `mysqldump` اکنون با `--databases` اجرا می‌شود تا dump خودکفا (شامل `CREATE DATABASE`/`USE`) باشد؛ برای بکاپ‌های قدیمی‌تر بدون این پرچم، این دستورات پیش از restore به‌صورت خودکار prepend می‌شوند.

## v4.2.2 — رفع خواندن manifest از سرور ریموت + آماده‌سازی MySQL

* خواندن `manifest.tsv` در مسیر انتقال به سرور جدید اکنون از طریق SSH روی خود سرور ریموت انجام می‌شود (قبلاً به‌اشتباه فایل محلی خوانده می‌شد و همیشه خالی بود).
* انتظار برای آماده‌شدن دیتابیس (`wait_db`) اکنون بر اساس نوع بک‌اند است (`pg_isready` برای Postgres/TimescaleDB، `mysqladmin ping` برای MySQL/MariaDB) — قبلاً همیشه `pg_isready` روی MySQL هم اجرا می‌شد و timeout می‌داد.
* manifest فقط وقتی نوشته می‌شود که **تمام** دامپ‌ها موفق باشند.

## v4.2.1 — سخت‌گیری امنیتی

* دایرکتوری موقت بکاپ از `/tmp` به `tempfile.mkdtemp()` با دسترسی `0700` منتقل شد.
* آپدیت خودکار: `curl | sudo bash` جایگزین شد با دانلود → تأیید SHA256 → اجرا.
* اعتبارسنجی سخت‌گیرانه‌ی نام Instance/سرویس Docker در همه‌ی مسیرهای `shell=True`.
* مسیر مقصد ریستور SQLite با `realpath` محدود به `/var/lib/pasarguard/` شد.
* نام فایل SQL در manifest در برابر path-traversal (`..`, `/`, `\`) رد می‌شود.

## v4.2 — امنیت و باگ‌های پایه

* رفع command injection در Manual Restore (نام فایل ZIP).
* رفع `MYSQL_PWD` که به کانتینر forward نمی‌شد — اکنون با `docker compose exec -e`.
* توکن بات/Chat ID دیگر در CLI args یا unit فایل نیست — فایل `0600` جدا.
* رمزها با `getpass` (بدون echo)؛ آرشیوهای بکاپ `chmod 600`.
* پشتیبانی از تقسیم/بازچسبانی خودکار فایل‌های تلگرام >50MB.
* Restart درجای شِدیولر بدون حذف/بازسازی + Update توکن درجا.

## v4.1 / v4.0 — پایه

* تشخیص و پشتیبانی کامل هر ۵ بک‌اند پنل رسمی PasarGuard (sqlite, postgresql, timescaledb, mysql, mariadb).
* بکاپ و ریستور **همه‌ی** دیتابیس‌های پاسارگارد، نه فقط `pasarguard`.

</div>
