# MytellDesk — میز کار اپراتور برای Issabel

MytellDesk یک میز کار تحت وب برای مشاهدهٔ وضعیت داخلی‌ها و صف‌ها، مدیریت تماس، گزارش تماس‌ها و ضبط‌ها، و تلفن مرورگری است. رابط فارسی و راست‌به‌چپ، تم روشن و تیره، دسترسی مدیر/سرپرست/اپراتور و ارتباط زنده با Asterisk دارد. این مخزن، محصول موجود MyTel را با نصب و به‌روزرسانی مستقل برای سرور Issabel ارائه می‌کند.

نصب روی همان سرور Issabel انجام می‌شود و محصول از نشانی `https://pbx.example.com/MytellDesk/` در دسترس است. دامنهٔ مثال را با نشانی HTTPS مرکز تلفن خود جایگزین کنید.

## نصب با یک فرمان

در شل **root روی سرور Issabel** اجرا کنید:

```bash
curl -fsSL https://raw.githubusercontent.com/DashSaman/MytelDesk/main/install.sh | bash
```

برای تعیین دامنهٔ HTTPS، به‌خصوص اگر با IP به SSH وصل شده‌اید:

```bash
curl -fsSL https://raw.githubusercontent.com/DashSaman/MytelDesk/main/install.sh | bash -s -- --public-url https://pbx.example.com
```

نصاب آخرین [انتشار](https://github.com/DashSaman/MytelDesk/releases) را دریافت و SHA-256 آرشیو و فایل‌های آن را بررسی می‌کند. مراحل نصب و نتیجهٔ بررسی سرویس، دیتابیس و AMI در ترمینال نمایش داده می‌شوند. بدون `--public-url`، نشانی موجود نصب قبلی یا نشانی محلی سرور انتخاب می‌شود.

پس از نصب، اطلاعات **حساب محلی مدیر MytellDesk** را با root بخوانید:

```bash
cat /etc/myteldesk/initial-admin.txt
```

در صفحهٔ ورود، گزینهٔ **حساب میز کار** (**Desk account**) را انتخاب کنید و با نام کاربری `admin` و رمز تصادفی همان فایل وارد شوید. گزینهٔ **حساب پنل** برای ورود با حساب Issabel است. فایل اطلاعات اولیه فقط برای root خواندنی است؛ آن را در محل امن نگه دارید. رمز عمومی مشترک وجود ندارد و راه‌اندازی مجدد سرویس، رمز کاربران موجود را بازنشانی نمی‌کند.

## پیش‌نیازها و سازگاری

| مورد | پیش‌نیاز نصاب |
|---|---|
| سیستم‌عامل | Rocky Linux 8 یا 9، معماری `x86_64` |
| مرکز تلفن | Issabel نصب‌شده با Asterisk 16، 18 یا 20 |
| سرویس‌ها | `asterisk`، `mariadb` و `httpd` فعال |
| Apache | ماژول‌های `proxy`، `proxy_http` و `proxy_wstunnel` |
| دسترسی | root، دسترسی محلی به MariaDB از تنظیمات Issabel و AMI فعال |
| دریافت فایل | دسترسی HTTPS به GitHub و nodejs.org |
| مرورگر | مرورگر به‌روز؛ HTTPS مورد اعتماد برای میکروفون و قابلیت‌های مرورگری |

این جدول، نسخه‌های پذیرفته‌شده در بررسی اولیهٔ نصاب را مشخص می‌کند. دامنهٔ نصب‌های واقعی و مسیرهای آزموده‌شدهٔ هر نسخه در یادداشت همان [انتشار](https://github.com/DashSaman/MytelDesk/releases) ثبت می‌شود؛ پذیرش نسخه توسط نصاب، نتیجهٔ آزمون همهٔ قابلیت‌های تلفنی روی آن نسخه نیست.

Asterisk 16 از **۹ اکتبر ۲۰۲۳** و Asterisk 18 از **۲۰ اکتبر ۲۰۲۵** به پایان پشتیبانی upstream رسیده‌اند. سازگاری MytellDesk با این نسخه‌ها به معنی دریافت به‌روزرسانی امنیتی برای خود Asterisk نیست. [چرخهٔ رسمی نسخه‌های Asterisk](https://docs.asterisk.org/About-the-Project/Asterisk-Versions/)

## محدودهٔ نصب

- نسخه‌های برنامه در `/opt/myteldesk/releases/` و Node.js 24 اختصاصی در `/opt/myteldesk/runtime/` قرار می‌گیرند؛ نسخهٔ فعال از `/opt/myteldesk/current` مشخص است.
- سرویس `myteldesk` با کاربر `asterisk` اجرا می‌شود. backend روی `127.0.0.1:8765` گوش می‌دهد و Apache موجود، مسیر `/MytellDesk/` را به آن متصل می‌کند.
- محصول از HTTPS و گواهی فعلی Issabel استفاده می‌کند. دامنه و گواهی معتبر را روی همان Apache آماده کنید؛ تعیین `--public-url` گواهی صادر نمی‌کند.
- دیتابیس و کاربر MariaDB با نام `myteldesk` و حساب AMI جداگانه ایجاد می‌شوند. حساب دیتابیس برای عملیات محصول به تنظیمات `asterisk` دسترسی خواندن/نوشتن و به `asteriskcdrdb` دسترسی خواندن دارد. حساب AMI به اتصال محلی محدود است.
- تنظیمات و اسرار در `/etc/myteldesk/` و دادهٔ قابل‌نوشتن برنامه در `/var/lib/myteldesk/` نگهداری می‌شوند. هش اولیهٔ مدیر فقط پس از ایجاد موفق حساب در جدول خالی کاربران مصرف می‌شود.
- نصب شامل `manager reload`، بارگذاری مجدد Apache و شروع یا راه‌اندازی مجدد سرویس MytellDesk است. نصاب Asterisk را restart نمی‌کند و ترانک‌ها را بازنویسی نمی‌کند.

پیش از نصب یا ارتقا، نسخهٔ پشتیبان قابل‌بازیابی از مرکز تلفن داشته باشید و زمان نگهداری مناسبی انتخاب کنید. به‌روزرسانی، سرویس خود MytellDesk را دوباره راه‌اندازی می‌کند؛ تداوم تماس و صوت باید متناسب با محیط شما بررسی شود.

نصاب سیاست firewall و حالت SELinux را تغییر نمی‌دهد. دسترسی کاربران را از مسیر HTTPS کنترل کنید و پورت backend یا AMI را برای اینترنت باز نکنید. روی میزبان SELinux enforcing، دسترسی لازم Apache به backend و دسترسی‌های سرویس باید در سیاست میزبان مجاز باشند.

## به‌روزرسانی و نگهداری

برای به‌روزرسانی، همان فرمان نصب را دوباره اجرا کنید. نصب متعلق به همین نصاب شناسایی می‌شود و داده‌ها، کاربران و اسرار موجود حفظ می‌شوند. پیش از ارتقای یک نصب آماده، فایل‌های تنظیمات تحت مدیریت نصاب و دیتابیس اختصاصی محصول در `/etc/myteldesk/backups/` پشتیبان‌گیری می‌شوند. این پشتیبان جایگزین بکاپ کامل PBX و فایل‌های ضبط نیست.

| فرمان با root | نتیجه |
|---|---|
| `myteldesk doctor` | بررسی سرویس، سلامت محلی backend، اتصال AMI، جداول برنامه، مدیر فعال و صحت تنظیم Apache |
| `myteldesk rollback` | بازگشت کد و پیکربندی آخرین ارتقا، در صورت وجود نسخهٔ قبلی؛ دادهٔ دیتابیس حفظ می‌شود |
| `myteldesk uninstall` | توقف سرویس و برداشتن اتصال‌های تحت مدیریت نصاب؛ دیتابیس، حساب‌ها، داده، اطلاعات ورود و پشتیبان‌ها باقی می‌مانند |
| `journalctl -u myteldesk -n 100 --no-pager` | مشاهدهٔ لاگ اخیر سرویس |

بازگردانی داده یا شمای دیتابیس، در صورت نیاز، فرایندی جداگانه با بررسی پشتیبان SQL است. اگر فایل‌های تحت مدیریت نصاب از خارج ویرایش شده باشند، بازگشت یا حذف خودکار متوقف می‌شود تا تغییرات جدید بازنویسی نشوند.

**نصب قدیمی OpDesk/MyTel:** وجود `/opt/opdesk`، `/opt/OpDesk` یا تنظیمات هم‌نام بدون سابقهٔ مالکیت این نصاب، نصب خودکار را متوقف می‌کند. مهاجرت چنین استقراری نیازمند بررسی جداگانهٔ کاربران، داده، تنظیمات و مسیرهای موجود است. برای عبور از بررسی اولیه، نصب قدیمی یا فایل مالکیت آن را حذف نکنید.

## تلفن مرورگری و قابلیت‌های اختیاری

WebRTC به داخلی و اطلاعات SIP معتبر، ماژول‌های مناسب Asterisk، TLS مورد اعتماد مرورگر، DTLS/ICE و مسیر صحیح رسانه از میان NAT نیاز دارد. مسیر WebSocket تلفنی محصول `/MytellDesk/sip-ws` است و به WebSocket محلی Asterisk متصل می‌شود. نصاب، ترانک یا اطلاعات داخلی‌های شما را به‌طور خودکار پیکربندی نمی‌کند. [راهنمای رسمی WebRTC در Asterisk](https://docs.asterisk.org/Configuration/WebRTC/Configuring-Asterisk-for-WebRTC-Clients/)

موفقیت `doctor` آمادگی سرویس و اتصال AMI را نشان می‌دهد. تماس ورودی و خروجی، انتقال و **صدای دوطرفه** باید با داخلی‌های مجاز و شبکهٔ واقعی شما جداگانه آزموده شوند.

تحلیل سکوت/گفتار **VAD اختیاری** است. سورس و فایل وابستگی‌های آن در `backend/vad/` همراه انتشار قرار دارند؛ نصاب محیط Python، مدل یا وابستگی‌های سنگین VAD را خودکار نصب نمی‌کند. اعلان Web Push، اعلان موبایل و اتصال CRM نیز به تنظیمات اختصاصی خود نیاز دارند.

## سورس، مجوز و توسعه

MytellDesk مشتق از [OpDesk اثر Ibrahim Gamal](https://github.com/Ibrahimgamal99/OpDesk) است و با **GPLv3** منتشر می‌شود. [LICENSE](LICENSE) و [UPSTREAM.md](UPSTREAM.md) شامل مجوز، منشأ و توضیح تغییرات هستند. وابستگی‌ها و فونت Vazirmatn مجوزهای خود را حفظ می‌کنند.

آرشیو هر انتشار شامل سورس متناظر frontend و backend، نصاب، فایل‌های قفل وابستگی، سورس VAD و متن مجوزهای وابستگی‌ها است. `SOURCE_TREE` نسخهٔ دقیق سورس و `manifest.json` هش فایل‌ها را مشخص می‌کنند؛ `SHA256SUMS` در صفحهٔ انتشار برای بررسی آرشیو ارائه می‌شود.

برای ساخت محلی، Node.js 24 و npm لازم است:

```bash
npm ci --prefix backend
npm ci --prefix frontend
npm run build --prefix backend
npm run build --prefix frontend
npm test --prefix backend -- --runInBand
node --test scripts/test-web-transport.cjs scripts/test-web-worker.cjs
```

تست‌های مرورگر در `scripts/test-web-browser.cjs` از برنامهٔ واقعی با API و WebSocket مصنوعی استفاده می‌کنند و به Playwright و Chrome نیاز دارند. `MT_WEB_PRODUCTION=1` آن‌ها را روی خروجی ساخته‌شدهٔ frontend اجرا می‌کند. ساخت انتشار با `scripts/build-release.py` از درخت Git تمیز انجام می‌شود و خروجی باید بیرون از مخزن باشد.

اسناد موجود در `docs/` از پروژهٔ upstream نگهداری شده‌اند و برای شناخت امکانات و API مفیدند. **دستورهای نصب، پورت‌ها و مسیرهای استقرار MytellDesk را از همین README دنبال کنید**؛ راهنمای نصب قدیمی upstream قرارداد این نصاب نیست.

## English quick reference

MytellDesk is the Persian/RTL MyTel operator workspace, distributed independently for an existing Issabel PBX. Run the one-line installer above **as root on the PBX**. It accepts Rocky Linux 8/9 x86_64 with Asterisk 16/18/20, installs a private Node.js 24 runtime, reuses Issabel's Apache/TLS endpoint, and serves `/MytellDesk/`.

Read `/etc/myteldesk/initial-admin.txt` as root, then choose **Desk account** on the sign-in screen. The generated local administrator password is unique; existing users are not reset on restart or update. Re-run the installer to update. Use `myteldesk doctor`, `myteldesk rollback` or `myteldesk uninstall` for maintenance; uninstall retains data and credentials.

Existing legacy OpDesk deployments require a separately reviewed migration. The installer reloads AMI configuration and Apache and restarts its own service; it does not restart Asterisk. See each release's notes for the actual tested host and feature scope. Live WebRTC calls and two-way audio require separate validation. VAD is optional and its Python environment is not installed automatically.

GPLv3; based on [OpDesk](https://github.com/Ibrahimgamal99/OpDesk). Complete corresponding source and third-party notices accompany release archives.
