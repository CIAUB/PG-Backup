# 📜 Changelog — PG-Backup

<div dir="rtl">

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
