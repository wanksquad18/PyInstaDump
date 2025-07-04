import logging
import time
import sys
import random
import csv
import signal

from playwright.sync_api import sync_playwright
from playwright._impl._errors import Error as PlaywrightError

from . import konstanta

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

class PengikisInstagram:
    """
    Kelas untuk mengotomatisasi proses scraping data followers atau
    following dari sebuah akun Instagram.
    """

    def __init__(self, target_username: str, mode_kikis: str, file_cookie: str) -> None:
        """
        Inisialisasi objek PengikisInstagram.

        Args:
            target_username: Username akun Instagram yang akan dikikis.
            mode_kikis: Mode operasi ('followers' atau 'following').
            file_cookie: Path menuju file cookie JSON.
        """
        self.target_username = target_username
        self.mode_kikis = mode_kikis.lower()
        self.file_cookie = file_cookie
        self.hasil_scrape = []
        self.output_file = None
        self._konfigurasi_mode()

        self.playwright = None
        self.browser = None
        self.page = None

        signal.signal(signal.SIGINT, self._signal_handler)

    def _konfigurasi_mode(self) -> None:
        """Mengatur path URL dan teks tombol berdasarkan mode yang dipilih."""
        mode_map = {
            'followers': (konstanta.PATH_FOLLOWERS, konstanta.TEKS_TOMBOL_FOLLOWERS),
            'following': (konstanta.PATH_FOLLOWING, konstanta.TEKS_TOMBOL_FOLLOWING)
        }
        try:
            self.url_path, self.teks_tombol = mode_map[self.mode_kikis]
        except KeyError:
            raise ValueError("Mode tidak valid. Pilih 'followers' atau 'following'.")

    def jalankan(self) -> list:
        """Memulai dan menjalankan seluruh alur proses scraping."""
        with sync_playwright() as p:
            self.playwright = p
            try:
                self._buka_browser()
                self._login_dengan_cookie()
                self._navigasi_ke_target()
                self._buka_popup_daftar()
                self._gulir_dan_muat_data()
                self._ekstrak_data_pengguna()

                if self.hasil_scrape:
                    logging.info(f"Scraping berhasil! Mengumpulkan {len(self.hasil_scrape)} data.")
            except KeyboardInterrupt:
                raise
            except PlaywrightError as e:
                logging.error(f"Terjadi kesalahan pada Playwright: {e}")
                logging.info("Mencoba menyimpan data yang sudah dikumpulkan...")
                self._auto_save_data()
            except Exception as e:
                logging.error(f"Terjadi kesalahan tidak terduga: {e}")
                logging.info("Mencoba menyimpan data yang sudah dikumpulkan...")
                self._auto_save_data()
            finally:
                self.tutup()

        return self.hasil_scrape

    def _buka_browser(self) -> None:
        """Membuka browser Chromium dan membuat halaman baru."""
        logging.info("Membuka browser...")
        self.browser = self.playwright.chromium.launch(headless=False, slow_mo=100)
        context = self.browser.new_context(
            storage_state=self.file_cookie,
            user_agent=konstanta.USER_AGENT
        )
        self.page = context.new_page()

    def _login_dengan_cookie(self) -> None:
        """Melakukan navigasi ke halaman utama Instagram untuk memvalidasi sesi login."""
        logging.info("Mencoba login menggunakan sesi dari file cookie...")
        for attempt in range(5):
            try:
                self.page.goto(konstanta.URL_DASAR, timeout=60000, wait_until="domcontentloaded")
                break
            except Exception as e:
                logging.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt == 4:
                    raise e
                time.sleep(5)
        
        logging.info("Berhasil memuat halaman utama Instagram.")

    def _navigasi_ke_target(self) -> None:
        """Membuka halaman profil dari target username."""
        url_target = f"{konstanta.URL_DASAR}/{self.target_username}/"
        logging.info(f"Navigasi ke profil target: {url_target}")
        self.page.goto(url_target, timeout=60000)

        try:
            self.page.wait_for_selector(konstanta.SELECTOR_HEADER_PROFIL, timeout=15000)
            logging.info("Header profil berhasil dimuat")
            
            time.sleep(5)
        except Exception as e:
            logging.warning(f"Warning: {e}")
            logging.info("Mencoba lanjutkan tanpa menunggu networkidle...")
            time.sleep(5)

        logging.info(f"Berhasil memuat profil {self.target_username}.")

    def _buka_popup_daftar(self) -> None:
        """Menemukan dan mengklik tombol followers/following untuk membuka dialog."""
        logging.info(f"Mencari dan mengklik tautan '{self.teks_tombol}'...")
        
        link_selectors = [
            f'a[href*="{self.url_path}"]',
            f'//a[contains(@href, "{self.url_path}")]',
            f'//a[contains(text(), "{self.teks_tombol}")]'
        ]
        
        clicked = False
        for selector in link_selectors:
            try:
                if selector.startswith('//'):
                    element = self.page.locator(selector).first
                else:
                    element = self.page.locator(selector).first

                element.click(timeout=5000)
                clicked = True
                logging.info(f"Berhasil klik {self.teks_tombol} dengan selector: {selector}")
                break

            except Exception as e:
                logging.warning(f"Selector {selector} gagal: {e}")
                continue
        
        if not clicked:
            logging.error(f"Gagal menemukan link {self.teks_tombol} secara otomatis")
            raise PlaywrightError(f"Tidak dapat menemukan tombol {self.teks_tombol}")
        
        logging.info(f"Menunggu pop-up {self.teks_tombol} muncul...")
        try:
            self.page.wait_for_selector(konstanta.SELECTOR_DIALOG_POPUP, timeout=15000)
            logging.info(f"Pop-up {self.teks_tombol} muncul.")
        except:
            logging.error("Pop-up tidak ditemukan")
            raise PlaywrightError(f"Pop-up {self.teks_tombol} tidak muncul")

    def _gulir_dan_muat_data(self) -> None:
        """Menggulir dialog untuk memuat semua data pengguna."""
        logging.info(f"Memulai proses menggulir untuk memuat daftar {self.teks_tombol}...")
        
        logging.info(f"Menunggu data {self.teks_tombol} dimuat...")
        time.sleep(10)
        
        try:
            count_text = self.page.locator(f'a[href*="{self.url_path}"]').inner_text()
            logging.info(f"Jumlah {self.teks_tombol}: {count_text}")
        except:
            logging.info(f"Tidak dapat mengambil jumlah {self.teks_tombol}")
        
        dialog_selector = konstanta.SELECTOR_DIALOG_POPUP
        scroll_attempts = 0
        previous_count = 0
        max_scrolls = 10000
        
        username_terproses = set()

        while scroll_attempts < max_scrolls:
            try:
                self.page.evaluate(f"""
                    const dialog = document.querySelector('{dialog_selector}');
                    if (dialog) {{
                        // Cari elemen scrollable yang tepat
                        const scrollableDiv = dialog.querySelector('div[style*="overflow-y: auto"]') || 
                                             dialog.querySelector('div[style*="overflow: auto"]') ||
                                             dialog.querySelector('div[style*="overflow-y: scroll"]') ||
                                             dialog.querySelector('div[style*="max-height"]');
                        
                        if (scrollableDiv) {{
                            // Scroll dengan smooth behavior
                            scrollableDiv.scrollBy({{
                                top: scrollableDiv.scrollHeight,
                                behavior: 'smooth'
                            }});
                        }} else {{
                            // Fallback ke dialog utama
                            dialog.scrollTop = dialog.scrollHeight;
                        }}
                    }}
                """)
                
                self.page.keyboard.press('PageDown')
                self.page.keyboard.press('PageDown')
                self.page.keyboard.press('End')
                self.page.mouse.wheel(0, 2000)
                
                try:
                    self.page.click(f'{dialog_selector}')
                    self.page.keyboard.press('PageDown')
                except:
                    pass
                
            except Exception as e:
                logging.error(f"Error saat scroll: {e}")
            
            time.sleep(random.uniform(2, 4))
            
            current_count = self.page.locator(f'{dialog_selector} a[href^="/"]').count()
            logging.info(f"{self.teks_tombol.capitalize()} dimuat: {current_count}")
            
            if current_count > previous_count:
                self._ekstrak_data_real_time(username_terproses)
            
            if current_count == previous_count:
                logging.info(f"Tidak ada {self.teks_tombol} baru yang dimuat")
                if scroll_attempts < 5:
                    scroll_attempts += 1
                    
                    try:
                        self.page.evaluate(f"""
                            // Scroll dengan metode yang berbeda
                            const dialog = document.querySelector('{dialog_selector}');
                            if (dialog) {{
                                const allDivs = dialog.querySelectorAll('div');
                                allDivs.forEach(div => {{
                                    if (div.scrollHeight > div.clientHeight) {{
                                        div.scrollTop = div.scrollHeight;
                                    }}
                                }});
                            }}
                        """)
                    except:
                        pass
                    
                    continue
                else:
                    break
            
            previous_count = current_count
            scroll_attempts += 1
            
            if current_count > 100000:
                logging.info(f"Sudah memuat {current_count} {self.teks_tombol}, berhenti scroll")
                break
            
            if scroll_attempts % 10 == 0:
                logging.info(f"Progress: {current_count} {self.teks_tombol} dimuat setelah {scroll_attempts} attempts")
        
        logging.info("Proses menggulir selesai.")
        
        logging.info("Menunggu sebelum mengambil data...")
        time.sleep(5)
        
        self._ekstrak_data_real_time(username_terproses)

    def _ekstrak_data_pengguna(self) -> None:
        """Mengekstrak username dan nama lengkap dari dialog yang telah dimuat."""
        logging.info("Mengekstrak data pengguna dari dialog (final check)...")
        
        if self.hasil_scrape:
            logging.info(f"Data sudah diekstrak real-time: {len(self.hasil_scrape)} items")
            return
        
        logging.info("Melakukan ekstraksi data manual...")
        username_terproses = set()
        self._ekstrak_data_real_time(username_terproses)
        
        logging.info(f"Total {len(self.hasil_scrape)} data pengguna berhasil diekstrak.")

    def simpan_ke_csv(self, nama_file: str) -> None:
        """
        Menyimpan data hasil scraping ke dalam sebuah file CSV.

        Args:
            nama_file: Path file CSV untuk menyimpan hasil.
        """
        if not self.hasil_scrape:
            logging.warning("Tidak ada data untuk disimpan.")
            return

        logging.info(f"Menyimpan {len(self.hasil_scrape)} data ke {nama_file}...")
        try:
            with open(nama_file, 'w', newline='', encoding='utf-8') as f:
                penulis = csv.writer(f)
                penulis.writerow(['Username', 'Full Name'])
                penulis.writerows(self.hasil_scrape)
            logging.info(f"Data berhasil disimpan ke {nama_file}.")
        except IOError as e:
            logging.error(f"Gagal menyimpan file: {e}")

    def set_output_file(self, output_file: str) -> None:
        """
        Set path file output untuk auto-save.
        
        Args:d
            output_file: Path file CSV untuk auto-save.
        """
        self.output_file = output_file

    def _signal_handler(self) -> None:
        """
        Handler untuk menangani sinyal interrupt (Ctrl+C).
        """
        logging.warning("\nProgram dihentikan oleh pengguna (Ctrl+C)")
        self._auto_save_data()
        self.tutup()
        sys.exit(0)

    def _auto_save_data(self):
        """
        Otomatis menyimpan data yang sudah dikumpulkan ke file CSV.
        """
        if self.hasil_scrape and self.output_file:
            parts = self.output_file.rsplit('.', 1)
            if len(parts) == 2:
                auto_save_file = f"{parts[0]}_partial.{parts[1]}"
            else:
                auto_save_file = f"{self.output_file}_partial"
                
            logging.info(f"Auto-save: Menyimpan {len(self.hasil_scrape)} data yang sudah dikumpulkan...")
            try:
                with open(auto_save_file, 'w', newline='', encoding='utf-8') as f:
                    penulis = csv.writer(f)
                    penulis.writerow(['Username', 'Full Name'])
                    penulis.writerows(self.hasil_scrape)
                logging.info(f"Data berhasil disimpan ke {auto_save_file}")
                logging.info(f"Total data yang tersimpan: {len(self.hasil_scrape)} {self.teks_tombol}")
            except IOError as e:
                logging.error(f"Gagal menyimpan auto-save: {e}")
        elif self.hasil_scrape:
            logging.warning("Ada data yang dikumpulkan tapi tidak ada path output yang di-set")
        else:
            logging.info("Tidak ada data yang dikumpulkan untuk disimpan")

    def tutup(self):
        """Menutup browser jika sedang berjalan."""
        if self.browser:
            logging.info("Menutup browser.")
            self.browser.close()
    
    def _ekstrak_data_real_time(self, username_terproses: set):
        """
        Ekstrak data pengguna secara real-time saat scroll.
        
        Args:
            username_terproses: Set untuk menghindari duplikasi username
        """
        try:
            containers = self.page.locator(f'{konstanta.SELECTOR_DIALOG_POPUP} div[style*="flex-direction"] > div')
            jumlah_container = containers.count()

            if jumlah_container == 0:
                containers = self.page.locator(f'{konstanta.SELECTOR_DIALOG_POPUP} a[href^="/"]')
                jumlah_container = containers.count()

            for i in range(jumlah_container):
                container = containers.nth(i)
                try:
                    username = ""
                    try:
                        link = container.locator('a[href^="/"]').first
                        href = link.get_attribute('href')
                        username = href.replace('/', '') if href else ""
                    except:
                        continue
                    
                    if not username or username in username_terproses:
                        continue
                    
                    nama_lengkap = ""
                    try:
                        spans = container.locator('span').all()
                        
                        for span in spans:
                            try:
                                text = span.inner_text().strip()
                                if not text or text == username:
                                    continue
                                
                                span_class = span.get_attribute('class') or ""
                                
                                if ('x1lliihq' in span_class or 
                                    'x193iq5w' in span_class or
                                    (len(text) > 2 and 
                                    not text.startswith('@') and 
                                    not text.endswith('K') and
                                    not text.lower() in ['follow', 'following', 'followers'])):
                                    nama_lengkap = text
                                    break
                                    
                            except:
                                continue
                                
                    except Exception as e:
                        logging.debug(f"Error mengambil full name untuk {username}: {e}")
                    
                    if username and username.strip() != "" and not (' ' in username or len(username) > 30):
                        logging.info(f"Diekstrak {self.teks_tombol} {len(self.hasil_scrape) + 1}: {username} - {nama_lengkap}")
                        self.hasil_scrape.append((username, nama_lengkap))
                        username_terproses.add(username)

                except Exception as e:
                    logging.debug(f"Gagal memproses item ke-{i}: {e}")
                    continue
                    
        except Exception as e:
            logging.warning(f"Error saat ekstraksi real-time: {e}")