# 🚀 PyInstaDump: Scraper Instagram Modular & Powerful!

![PyInstaDump - Logo](https://github.com/user-attachments/assets/bf39cb99-48a5-4b02-8026-c4ffdd3376ad)

`PyInstaDump` adalah sebuah alat bantu (*tool*) berbasis Python yang dirancang untuk melakukan *scraping* (pengikisan data) daftar *followers* atau *following* dari sebuah akun publik di Instagram. Proyek ini dibangun dengan menerapkan prinsip-prinsip *Object-Oriented Programming* (OOP) untuk memastikan kode yang bersih, modular, dan mudah dikelola.

## Fitur Utama

- **Scraping Fleksibel**: Dapat mengambil data *followers* maupun *following*.
- **Otentikasi Berbasis Cookie**: Menggunakan sesi *login* yang aman melalui *cookie* untuk mengakses data.
- **Output CSV**: Menyimpan hasil *scraping* dalam format `.csv` yang rapi dengan kolom `Username` dan `Full Name`.
- **Antarmuka Baris Perintah (CLI)**: Interaksi yang mudah melalui argumen di terminal.
- **Logging Profesional**: Memberikan informasi status proses yang jelas dan informatif.

## Struktur Proyek

Proyek ini disusun dengan struktur yang terorganisir untuk memisahkan setiap komponen logika:

```
PyInstaDump/
├── pyinstadump/
│   ├── __init__.py
│   ├── main.py             # Titik masuk aplikasi (CLI)
│   ├── pengikis.py         # Kelas inti untuk logika scraping
│   ├── utilitas.py         # Fungsi bantuan (misal: parser cookie)
│   └── konstanta.py        # Penyimpanan nilai konstan
│
├── data/
│
├── requirements.txt        # Dependensi proyek
└── README.md
```

## Cara Menjalankan

### 1. Prasyarat

- Python 3.13 atau lebih baru.
- Browser Google Chrome atau Chromium terinstal.

### 2. Instalasi

a. **Kloning Repositori**

```bash
git clone https://github.com/RozhakLabs/PyInstaDump.git
cd PyInstaDump
```

b. **Instal Dependensi Python**

```bash
pip install -r requirements.txt
```

c. **Instal Browser Playwright**
   Perintah ini akan mengunduh *driver browser* yang dibutuhkan oleh Playwright.

```bash
playwright install
```

### 3. Mendapatkan Cookie Instagram

Untuk menjalankan *scraper*, Anda memerlukan *cookie* dari sesi *login* Anda di Instagram.

a. Buka Instagram di browser Anda dan *login*.   
b. Buka *Developer Tools* (biasanya dengan F12 atau Ctrl+Shift+I).   
c. Pergi ke tab **Network** (Jaringan).   
d. Muat ulang halaman, cari permintaan ke `www.instagram.com` dengan tipe `document`.   
e. Di bagian *Headers* -> *Request Headers*, cari baris `cookie:`.   
f. Salin seluruh nilai *string* dari *cookie* tersebut.   

### 4. Menjalankan Scraper

Gunakan perintah berikut di terminal Anda, ganti nilai argumen sesuai kebutuhan.

```bash
python -m pyinstadump.main --username <NAMA_TARGET> --mode <followers/following> --cookie "<PASTE_COOKIE_STRING_ANDA_DI_SINI>"
```

**Contoh:**

```bash
python -m pyinstadump.main --username rozhak_official --mode following --cookie "csrftoken=...; sessionid=...;"
```

Hasil *scraping* akan secara otomatis tersimpan di dalam folder `data/`.

## Lisensi

Proyek ini didistribusikan di bawah lisensi MIT. Anda bebas menggunakan, memodifikasi, dan mendistribusikan ulang perangkat lunak ini, selama mencantumkan atribusi kepada pembuat asli. Untuk detail lengkap, silakan lihat file [LICENSE](LICENSE) di repositori ini.