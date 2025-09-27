import sys, socket, time, os, datetime, serial, serial.tools.list_ports, re
from PyQt5 import uic, QtWidgets
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QThreadPool
from PyQt5.QtWidgets import QApplication, QMainWindow, QGraphicsScene, QGraphicsView
from PyQt5.QtGui import QImage, QPixmap, QFont
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

# Kamera (RPi varsa kullanılacak)
try:
    from picamera2.picamera2 import Picamera2, libcamera
    HAS_PI_CAM = True
except Exception:
    Picamera2 = None
    libcamera = None
    HAS_PI_CAM = False

# ---------------- Yardımcı: güvenli seri gönder/al ----------------
def normalize_line(s: str) -> str:
    return s.strip()

def _upper(s: str) -> str:
    return s.strip().upper()

def is_interesting(s: str) -> bool:
    
    """DONE/OK/PONG/ERR ve ölçüm ön ekleri (WEIGHT:/PH:/RAW:) — case-insensitive."""
    if not s:
        return False
    su = _upper(s)
    return su.startswith(("DONE", "OK", "PONG", "ERR", "WEIGHT:", "PH:", "RAW:"))

def startswith_token(line: str, token_prefix: str) -> bool:
    return _upper(line).startswith(_upper(token_prefix))


class SerialWorker:
    """UI thread içinde kısa bloklar için basit yardımcı."""
    def __init__(self, ser: serial.Serial):
        self.ser = ser

    def send_command(self, cmd: str, wait_token_prefix: str = None, timeout_s: float = 5.0):
        """
        cmd -> Arduino'ya gönderir. wait_token_prefix verilirse bu prefix ile başlayan satırı bekler.
        Yoksa DONE/OK/PONG/ERR/WEIGHT:/PH:/RAW: gibi 'ilginç' ilk satırı döndürür.
        """
        if not self.ser or not self.ser.is_open:
            return None
        if not cmd.endswith("\n"):
            cmd = cmd + "\n"
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass

        self.ser.write(cmd.encode())

        start = time.time()
        last_line = None
        while time.time() - start < timeout_s:
            try:
                if self.ser.in_waiting:
                    line = normalize_line(self.ser.readline().decode(errors="ignore"))
                    if not line:
                        continue
                    if is_interesting(line):
                        last_line = line
                        if wait_token_prefix is not None:
                            if startswith_token(line, wait_token_prefix):
                                return line
                        else:
                            return line
                time.sleep(0.02)
            except Exception as e:
                return f"ERR: {e}"
        return last_line or "ERR: TIMEOUT"


# ---------------- TCP İstemci Thread ----------------
class TcpClientThread(QThread):
    data_received = pyqtSignal(str)
    connection_error = pyqtSignal(str)

    def __init__(self, server_ip: str, server_port: int):
        super().__init__()
        self.server_ip = server_ip
        self.server_port = server_port
        self.running = True

    def run(self):
        while self.running:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(5.0)                    # sadece connect için
                    s.connect((self.server_ip, self.server_port))
                    s.settimeout(None)                   # bağlandıktan sonra bloklayıcı
                    while self.running:
                        data = s.recv(4096)              # veri gelene kadar bekler
                        if not data:
                            raise ConnectionError("Empty TCP read")
                        self.data_received.emit(data.decode(errors="ignore"))
            except Exception as e:
                self.connection_error.emit(f"Bağlantı hatası: {e}")
                time.sleep(3)


# ---------------- Kamera Thread ----------------
class CameraThread(QThread):
    update_image = pyqtSignal(QImage, bytes)

    def __init__(self):
        super().__init__()
        self.picam2 = None

    def init_camera(self):
        if not HAS_PI_CAM:
            return False
        try:
            info = Picamera2.global_camera_info()
            if not info:
                return False
            self.picam2 = Picamera2()
            config = self.picam2.create_still_configuration(
                main={"size": (320, 240), "format": "RGB888"}
            )
            config["transform"] = libcamera.Transform(hflip=True, vflip=True)
            self.picam2.configure(config)
            self.picam2.start()
            return True
        except Exception:
            self.picam2 = None
            return False

    def run(self):
        if not self.init_camera():
            return
        while True:
            try:
                frame = self.picam2.capture_array()  # numpy HxWxC
                h, w, c = frame.shape

                # Önce BGR varsay ve Qt'nin BGR888’ini dene:
                try:
                    qimg = QImage(frame.data, w, h, 3*w, QImage.Format_BGR888)
                except AttributeError:
                    # Qt sürümünde BGR888 yoksa R<->B swap yapıp RGB888 kullan
                    frame = frame[..., [2,1,0]].copy()
                    qimg = QImage(frame.data, w, h, 3*w, QImage.Format_RGB888)

                # Bellek güvenliği için kopyasını yolla
                self.update_image.emit(qimg.copy(), frame.tobytes())
            except Exception:
                time.sleep(0.2)


# ---------------- Ana Uygulama ----------------
class MyApp(QMainWindow):
    def __init__(self):
        super().__init__()

        self.thread_pool = QThreadPool.globalInstance()
        self.ser = None
        self.worker = None

        # UI'yi mutlak yolla yükle
        ui_path = APP_DIR / "frontend_titration_main.ui"
        if not ui_path.exists():
            print("UI bulunamadı:", ui_path)
            print("Klasör içeriği:", [p.name for p in APP_DIR.iterdir()])
            raise FileNotFoundError(f"UI dosyası yok: {ui_path}")
        uic.loadUi(str(ui_path), self)
        
        # Ana sayfa (tab_main) ile başlat
        if hasattr(self, "mainPage") and hasattr(self, "tab_main"):
            self.mainPage.setCurrentWidget(self.tab_main)
            
        # Durum değişkenleri
        self.rgb_received = False
        self.motor3_working = False
        self.last_camera_process_time = 0.0
        self.current_rgb = None
        self.test_in_progress = False
        self.successful_tests_count = 0
        self.motor_units = {"motor1": "ml", "motor2": "ml", "motor3": "ml"}
        self.motor_resolution = {"motor1": 8526.32, "motor2": 8526.32, "motor3": 8526.32}
        self.motor3_preload_done = False

        # Dev sayfası ON/OFF state
        self.air_on = False
        self.water_on = False
        self.valve_on = False

        # Formül varsayılanları
        self.formul_air_pump_time = 5
        self.formul_water_pump_time = 3
        self.formul_selenoid_valve_time = 3
        self.formul_cokme_valve_time = 10
        self.math_formul = ""
        self.math_constants = {"N": 1, "F": 1, "3": 3}

        # Sahne/ görüntü
        self.scene = QGraphicsScene(self)
        self.graphics_view_1 = self.findChild(QGraphicsView, 'graphicsView_1')
        self.graphics_view_2 = self.findChild(QGraphicsView, 'graphicsView_2')
        self.graphics_view_3 = self.findChild(QGraphicsView, 'graphicsView_3')
        for gv in (self.graphics_view_1, self.graphics_view_2, self.graphics_view_3):
            if gv:
                gv.setScene(self.scene)

        # Kamera ve TCP
        self.camera_thread = CameraThread()
        self.camera_thread.update_image.connect(self.update_graphics_view)
        self.tcp_thread = TcpClientThread('192.158.56.1', 9876)  # Gerekirse IP'yi değiştir

        self.setup_signals()
        self.select_com_port()

        # Başlat
        self.tcp_thread.start()
        self.camera_thread.start()


        # Sayfa geçiş butonları (QTabWidget: mainPage)
        if hasattr(self, "olcum_pushButton") and hasattr(self, "mainPage") and hasattr(self, "tab_measure"):
            self.olcum_pushButton.clicked.connect(lambda: self.mainPage.setCurrentWidget(self.tab_measure))
        if hasattr(self, "gelistirici_pushButton") and hasattr(self, "mainPage") and hasattr(self, "tab_dev"):
            self.gelistirici_pushButton.clicked.connect(lambda: self.mainPage.setCurrentWidget(self.tab_dev))
        if hasattr(self, "formul_pushButton") and hasattr(self, "mainPage") and hasattr(self, "tab_formul"):
            self.formul_pushButton.clicked.connect(lambda: self.mainPage.setCurrentWidget(self.tab_formul))
        if hasattr(self, "yogunluk_pushButton") and hasattr(self, "mainPage") and hasattr(self, "tab_density"):
            self.yogunluk_pushButton.clicked.connect(lambda: self.mainPage.setCurrentWidget(self.tab_density))
        if hasattr(self, "ph_pushButton") and hasattr(self, "mainPage") and hasattr(self, "tab_ph"):
            self.ph_pushButton.clicked.connect(lambda: self.mainPage.setCurrentWidget(self.tab_ph))
        if hasattr(self, "bulaniklik_pushButton") and hasattr(self, "mainPage") and hasattr(self, "tab_bulaniklik"):
            self.bulaniklik_pushButton.clicked.connect(lambda: self.mainPage.setCurrentWidget(self.tab_bulaniklik))

        self.loadFormulas()

    # ---------- Genel ----------
    @staticmethod
    def clean_exit():
        QThreadPool.globalInstance().clear()

    def closeEvent(self, event):
        try:
            self.tcp_thread.running = False
            self.tcp_thread.wait(1000)
        except Exception:
            pass
        event.accept()

    def select_com_port(self):
        ports = list(serial.tools.list_ports.comports())
        port_name = None
        if ports:
            port_name = ports[0].device
        if port_name is None:
            port_name = '/dev/ttyUSB0'  # Arduino buradaysa sabitle
        try:
            self.ser = serial.Serial(port_name, 9600, timeout=0.2)
            self.worker = SerialWorker(self.ser)
            print(f"Seri port bağlandı: {port_name}")
        except Exception as e:
            print(f"Seri bağlanamadı: {e}")
            self.ser = None
            self.worker = None

    # ---------- Sinyaller ----------
    def setup_signals(self):
        self.tcp_thread.data_received.connect(self.process_camera_data)
        self.tcp_thread.connection_error.connect(self.handle_connection_error)

        # Ölçüm sayfası
        if hasattr(self, "formula_combobox"):
            self.formula_combobox.currentIndexChanged.connect(self.loadFormula)
        if hasattr(self, "preProcess_button"):
            self.preProcess_button.clicked.connect(self.preprocess)
        if hasattr(self, "start_test_button"):
            self.start_test_button.clicked.connect(self.start_test)
        if hasattr(self, "complete_button"):
            self.complete_button.clicked.connect(self.complete_test)
        if hasattr(self, "report_button"):
            self.report_button.clicked.connect(self.save_report)
        if hasattr(self, "clean_button"):
            self.clean_button.clicked.connect(self.clean_system)

        # Dev sayfası
        if hasattr(self, "dev_motor1_button"):
            self.dev_motor1_button.clicked.connect(lambda: self.control_motor1(self.dev_motor1_input.text()))
        if hasattr(self, "dev_motor2_button"):
            self.dev_motor2_button.clicked.connect(lambda: self.control_motor2(self.dev_motor2_input.text()))
        if hasattr(self, "dev_motor3_button"):
            self.dev_motor3_button.clicked.connect(lambda: self.control_motor3(self.dev_motor3_input.text()))
        if hasattr(self, "dev_air_pump_onoff"):
            self.dev_air_pump_onoff.clicked.connect(self.toggle_air_pump)
        if hasattr(self, "dev_water_pump_onoff"):
            self.dev_water_pump_onoff.clicked.connect(self.toggle_water_pump)
        if hasattr(self, "dev_selenoid_valve_onoff"):
            self.dev_selenoid_valve_onoff.clicked.connect(self.toggle_selenoid_valve)
        if hasattr(self, "dev_air_pump_button"):
            self.dev_air_pump_button.clicked.connect(lambda: self.trigger_air_pump(self.dev_air_pump_input.text()))
        if hasattr(self, "dev_water_pump_button"):
            self.dev_water_pump_button.clicked.connect(lambda: self.trigger_water_pump(self.dev_water_pump_input.text()))
        if hasattr(self, "dev_selenoid_valve_button"):
            self.dev_selenoid_valve_button.clicked.connect(lambda: self.trigger_selenoid_valve(self.dev_selenoid_valve_input.text()))
        if hasattr(self, "dev_camera_button"):
            self.dev_camera_button.clicked.connect(self.control_camera)

        # Yoğunluk/pH sekmesi
        if hasattr(self, "weight_button"):
            self.weight_button.clicked.connect(self.get_weight)
        if hasattr(self, "calculate_button"):
            self.calculate_button.clicked.connect(self.calculate_density)
        if hasattr(self, "ph_button"):
            self.ph_button.clicked.connect(self.get_ph)

        # Formül sekmesi
        if hasattr(self, "save_formul_button"):
            self.save_formul_button.clicked.connect(self.saveFormula)
        if hasattr(self, "formul_load_button"):
            self.formul_load_button.clicked.connect(self.loadFormula)

    # ---------- Seri: Non-blocking gönderim yardımcı ----------
    def send_nowait(self, cmd: str):
        """Serial'e beklemeden komut gönder (UI'yi bloklama)."""
        if self.ser and self.ser.is_open:
            if not cmd.endswith("\n"):
                cmd += "\n"
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
            self.ser.write(cmd.encode())
            return True
        return False

    # ---------- Görüntü ----------
    def update_graphics_view(self, qImg: QImage, raw_data: bytes):
        self.scene.clear()
        # sadece görüntü amaçlı; sayısal RGB'yi FQ2 veriyor
        self.scene.addPixmap(QPixmap.fromImage(qImg))
        for gv in (self.graphics_view_1, self.graphics_view_2, self.graphics_view_3):
            if gv:
                gv.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)

    # ---------- Ölçüm Akışı ----------
    def preprocess(self):
        self.set_status("Hazırlık")
        for i, ml in enumerate((38000, 38000, 38000), start=1):
            if self.worker:
                self.worker.send_command(f"MOVE{i} {ml}", "DONE", 15.0)

    def start_test(self):
        self.test_in_progress = True
        self.current_rgb = None
        self.rgb_received = False
        self.clear_rgb_lcds()
        self.set_status("Test başlatıldı")
        try:
            ml1 = float(self.sample_input.text().replace(',', '.'))
            ml2 = float(self.indicator_input.text().replace(',', '.'))
            self.control_motor1(ml1)
            self.control_motor2(ml2)
            QTimer.singleShot(3000, self.repeat_actions)
        except Exception:
            self.set_status("Geçersiz giriş")

    def repeat_actions(self):
        if not self.test_in_progress or self.motor3_working:
            return
        if not self.motor3_preload_done:
            pv = self.formul_motor3_preload_input.text()
            try:
                val = float(pv.replace(',', '.')) if pv else 0
                if val > 0:
                    self.control_motor3(val)
                    self.motor3_preload_done = True
            except Exception:
                pass
        self.motor3_working = True
        ml3 = float(self.titrant_input.text().replace(',', '.'))
        self.control_motor3(ml3)
        QTimer.singleShot(3000, self.after_motor3)

    def after_motor3(self):
        if not self.motor3_working:
            return
        air_s = float(self.formul_air_pump_time or 5)
        self.trigger_air_pump(air_s)
        QTimer.singleShot(int(air_s * 1000), self.after_air_pump_done)

    def after_air_pump_done(self):
        # Çökme süresi
        try:
            c = self.formul_cokme_valve_time
            if isinstance(c, str):
                cokme_ms = int(float(c.replace(',', '.')) * 1000)
            else:
                cokme_ms = int(float(c) * 1000)
        except Exception:
            cokme_ms = 3000

        self.send_nowait(f"COKME_DUR {cokme_ms}")
        QTimer.singleShot(cokme_ms, self.trigger_camera)

    # ---------- Kamera tetik ----------
    def control_camera(self):
        """Arduino'ya kamera tetik komutu gönderir."""
        if self.worker:
            self.worker.send_command("CAMERA_TRIG", "DONE", 3.0)
            self.set_status("Kamera tetiklendi.")

    def trigger_camera(self):
        self.control_camera()

    def camera_triggered(self):
        pass  # kullanılmıyor

    def check_and_repeat_rgb(self):
        if not (self.test_in_progress and self.rgb_received):
            return
        r, g, b = self.get_current_rgb()
        trg = self.read_target_rgb()
        if not trg:
            return
        tr, tg, tb = trg

        # Artı thresholdlar
        thr_r_plus = int(self.formul_threshold_input_R.text() or 20)
        thr_g_plus = int(self.formul_threshold_input_G.text() or 20)
        thr_b_plus = int(self.formul_threshold_input_B.text() or 20)
        # Eksi thresholdlar
        thr_r_minus = int(self.formul_threshold_input_R_2.text() or thr_r_plus) if hasattr(self, "formul_threshold_input_R_2") else thr_r_plus
        thr_g_minus = int(self.formul_threshold_input_G_2.text() or thr_g_plus) if hasattr(self, "formul_threshold_input_G_2") else thr_g_plus
        thr_b_minus = int(self.formul_threshold_input_B_2.text() or thr_b_plus) if hasattr(self, "formul_threshold_input_B_2") else thr_b_plus

        ok = (tr - thr_r_minus <= r <= tr + thr_r_plus and
              tg - thr_g_minus <= g <= tg + thr_g_plus and
              tb - thr_b_minus <= b <= tb + thr_b_plus)
        if ok:
            self.set_status("Hedef RGB’ye ulaşıldı")
            self.complete_test()
        else:
            self.set_status(f"RGB hedefte değil: ({r},{g},{b})")
            self.motor3_working = False
            self.repeat_actions()

    def complete_test(self):
        if not self.test_in_progress:
            return
        self.test_in_progress = False
        self.motor3_working = False
        self.motor3_preload_done = False
        self.set_status("Test tamamlandı.")
        if self.current_rgb:
            r, g, b = self.current_rgb
            self.save_report(r, g, b)
            self.calculate_math_formula_result()  
        self.successful_tests_count = 0
        self.current_rgb = None
        self.rgb_received = False

    def calculate_math_formula_result(self):
        """
        Ölçüm sayfasındaki math_formul_input alanındaki formülü değerlendirir.
        M1, M2, M3 değişkenleri ile sonucu hesaplar ve graphicsView_output'a yazar.
        """
        try:
            # Motor sarfiyatlarını al
            M1 = float(self.sample_input.text().replace(',', '.') or 0)
            M2 = float(self.indicator_input.text().replace(',', '.') or 0)
            preload = float(self.formul_motor3_preload_input.text().replace(',', '.') or 0)
            titrant = float(self.titrant_input.text().replace(',', '.') or 0)
            repeat_count = max(1, self.successful_tests_count)
            M3 = preload + titrant * repeat_count

            # Formülü al
            formula = self.math_formul_input.text()
            # Güvenli ortamda değerlendir
            allowed_names = {"M1": M1, "M2": M2, "M3": M3}
            result = eval(formula, {"__builtins__": None}, allowed_names)

            # Sonucu graphicsView_output'a yaz
            if hasattr(self, "graphicsView_output") and self.graphicsView_output is not None:
                sc = QGraphicsScene()
                sc.addText(f"Formül Sonucu: {result:.2f}")
                self.graphicsView_output.setScene(sc)
            else:
                print(f"Formül Sonucu: {result:.2f}")

            # Tekrar sayısını status_label'a yaz
            if hasattr(self, "status_label") and self.status_label is not None:
                self.status_label.setText(f"Tespit edilen tekrar sayısı: {repeat_count}")

        except Exception as e:
            if hasattr(self, "graphicsView_output") and self.graphicsView_output is not None:
                sc = QGraphicsScene()
                sc.addText(f"Formül hatası: {e}")
                self.graphicsView_output.setScene(sc)
            else:
                print(f"Formül hatası: {e}")

    # ---------- Temizlik ----------
    def clean_system(self):
        self.set_status("TEMİZLİK")
        try:
            valve_ms = int(float(str(self.formul_selenoid_valve_time).replace(',', '.')) * 1000)
            water_ms = int(float(str(self.formul_water_pump_time).replace(',', '.')) * 1000)
            air_ms   = int(float(str(self.formul_air_pump_time).replace(',', '.')) * 1000)
        except Exception:
            valve_ms, water_ms, air_ms = 3000, 3000, 5000

        if not self.worker:
            return
        self.worker.send_command("VALVE_ON", "DONE", 2.0)
        QTimer.singleShot(valve_ms, lambda: self.worker.send_command("VALVE_OFF", "DONE", 2.0))
        QTimer.singleShot(valve_ms + 1000, lambda: self.worker.send_command("AIR_ON", "DONE", 2.0))
        QTimer.singleShot(valve_ms + 1000, lambda: self.worker.send_command("WATER_ON", "DONE", 2.0))
        QTimer.singleShot(valve_ms + water_ms + 1000, lambda: self.worker.send_command("WATER_OFF", "DONE", 2.0))
        QTimer.singleShot(valve_ms + water_ms + 1500, lambda: self.worker.send_command("VALVE_ON", "DONE", 2.0))
        QTimer.singleShot(valve_ms + air_ms + water_ms + 2000, lambda: (
            self.worker.send_command("AIR_OFF", "DONE", 2.0),
            self.worker.send_command("VALVE_OFF", "DONE", 2.0)
        ))

    # ---------- TCP/Kamera Veri ----------
    def process_camera_data(self, data: str):
        # Ham mesaj:
        print("FQ2 RAW:", repr(data))

        now = time.time()
        if now - self.last_camera_process_time < 0.2:
            return
        self.last_camera_process_time = now

        # 1) FQ2 -> 3 satır float (R,G,B)
        lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
        r = g = b = None
        try:
            if len(lines) >= 3:
                r = float(lines[0]); g = float(lines[1]); b = float(lines[2])
            else:
                # 2) Fallback: tek satır / virgüllü
                toks = re.findall(r'-?\d+(?:\.\d+)?', data)
                if len(toks) >= 3:
                    r, g, b = map(float, toks[:3])
        except Exception:
            pass

        if r is None or g is None or b is None:
            print("RGB verisi anlaşılamadı:", data)
            self.rgb_received = False
            return

        # Yuvarla ve 0..255
        r = max(0, min(255, int(round(r))))
        g = max(0, min(255, int(round(g))))
        b = max(0, min(255, int(round(b))))

        # LCD'ler
        if hasattr(self, "lcdNumber_Pointer_R"):
            self.lcdNumber_Pointer_R.display(r)
            self.lcdNumber_Pointer_G.display(g)
            self.lcdNumber_Pointer_B.display(b)
        if hasattr(self, "lcdNumber_Pointer_R_Dev"):
            self.lcdNumber_Pointer_R_Dev.display(r)
            self.lcdNumber_Pointer_G_Dev.display(g)
            self.lcdNumber_Pointer_B_Dev.display(b)

        QApplication.processEvents()

        # Durum ve hedef kontrol
        self.current_rgb = (r, g, b)
        self.rgb_received = True
        if self.test_in_progress:
            self.successful_tests_count += 1
            if hasattr(self, "status_label"):
                self.status_label.setText(f"Transfer count: {self.successful_tests_count}")
        self.check_and_repeat_rgb()

    def handle_connection_error(self, error: str):
        self.set_status(error)

    # ---------- Kayıt / Formül ----------
    def save_report(self, r=None, g=None, b=None):
        if self.current_rgb:
            r, g, b = self.current_rgb
        if r is None or g is None or b is None:
            self.set_status("RGB yok, rapor kaydedilemedi.")
            return
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        d = os.path.join("reports", now.split(" ")[0])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "report.txt"), "a") as f:
            f.write(f"{now}, RGB: ({r}, {g}, {b})\n")
        self.set_status("Rapor kaydedildi.")

    # ---- yardımcı: güvenli yazı çıkışı (math vs. için) ----
    def _set_scene_text(self, view_attr: str, text: str):
        try:
            view = getattr(self, view_attr, None)
            if view is not None:
                sc = QGraphicsScene()
                sc.addText(text)
                view.setScene(sc)
            elif hasattr(self, "status_label") and self.status_label is not None:
                self.status_label.setText(text)
            else:
                print(text)
        except Exception as e:
            print("Output yazılamadı:", e, "->", text)

    # ---- FORMÜL KAYDET (tek şema) ----
    def saveFormula(self):
        try:
            name = self.formul_name_input.text()

            air_txt   = getattr(self, "formul_air_pump_input_2", getattr(self, "formul_air_pump_input", None)).text()
            water_txt = getattr(self, "formul_water_pump_input_2", getattr(self, "formul_water_pump_input", None)).text()
            selen_txt = getattr(self, "formul_selenoid_valve_input_2", getattr(self, "formul_selenoid_valve_input", None)).text()

            m4_txt = getattr(self, "formul_motor4_input").text() if hasattr(self, "formul_motor4_input") else "0"
            m5_txt = getattr(self, "formul_motor5_input").text() if hasattr(self, "formul_motor5_input") else "0"

            # Artı ve eksi thresholdlar
            thrR_plus = self.formul_threshold_input_R.text()
            thrG_plus = self.formul_threshold_input_G.text()
            thrB_plus = self.formul_threshold_input_B.text()
            thrR_minus = self.formul_threshold_input_R_2.text() if hasattr(self, "formul_threshold_input_R_2") else thrR_plus
            thrG_minus = self.formul_threshold_input_G_2.text() if hasattr(self, "formul_threshold_input_G_2") else thrG_plus
            thrB_minus = self.formul_threshold_input_B_2.text() if hasattr(self, "formul_threshold_input_B_2") else thrB_plus

            data = [
                name,
                self.formul_motor1_input.text(),
                self.formul_motor2_input.text(),
                self.formul_motor3_input.text(),
                self.formul_motor3_preload_input.text(),
                m4_txt,
                m5_txt,
                air_txt,
                water_txt,
                selen_txt,
                self.formul_cokme_valve_input.text(),
                self.formul_target_input_R.text(),
                self.formul_target_input_G.text(),
                self.formul_target_input_B.text(),
                thrR_plus,
                thrG_plus,
                thrB_plus,
                thrR_minus,
                thrG_minus,
                thrB_minus,
                getattr(self, "math_formul_input").text() if hasattr(self, "math_formul_input") else ""
            ]

            # Aynı isimliyse üzerine yaz
            lines = []
            try:
                with open('formulas.txt', 'r') as f:
                    for line in f:
                        parts = line.strip().split(',')
                        if parts and parts[0] != name:
                            lines.append(line.strip())
            except FileNotFoundError:
                pass
            lines.append(','.join(data))
            with open('formulas.txt', 'w') as f:
                for ln in lines:
                    f.write(ln + '\n')

            if self.formula_combobox.findText(name) == -1:
                self.formula_combobox.addItem(name)
            self.set_status("Formül kaydedildi.")
        except Exception as e:
            self.set_status(f"Formül kaydedilemedi: {e}")

    # ---- FORMÜL YÜKLE ----
    def loadFormula(self):
        sel = self.formula_combobox.currentText()
        try:
            with open('formulas.txt', 'r') as f:
                for line in f:
                    parts = line.strip().split(',')
                    if parts and parts[0] == sel:
                        self.apply_formula(parts)
                        break
            self.set_status("Formül yüklendi.")
        except Exception as e:
            self.set_status(f"Formül yüklenemedi: {e}")

    def loadFormulas(self):
        try:
            with open('formulas.txt', 'r') as f:
                for line in f:
                    name = line.strip().split(',')[0]
                    if name and self.formula_combobox.findText(name) == -1:
                        self.formula_combobox.addItem(name)
        except FileNotFoundError:
            pass

    # ---- FORMÜL UYGULA (toleranslı) ----
    def apply_formula(self, p):
        """
        v3 şeması: name,m1,m2,m3,m3_preload,m4,m5,air,water,selenoid,cokme,R,G,B,thrR+,thrG+,thrB+,thrR-,thrG-,thrB-,math
        Fazla kolonları yok sayar, eksiklerde varsayılan kullanır.
        """
        p = list(p)
        # Alan sayısı 21 olmalı (0-20 arası)

        def get(i, dflt=""):
            return p[i] if i < len(p) else dflt
        def fnum(x, d):
            try:
                return float(str(x).replace(',', '.')) if str(x) != "" else d
            except Exception:
                return d

        if len(p) < 2:
            return

        name = get(0)
        m1   = get(1);  m2 = get(2);  m3 = get(3);  m3pre = get(4)
        m4   = get(5, "0")
        m5   = get(6, "0")
        air  = get(7, "1")
        water= get(8, "1")
        selen= get(9, "1")
        cokme= get(10, "1")
        R, G, B = get(11, "0"), get(12, "0"), get(13, "0")
        thrR_plus, thrG_plus, thrB_plus = get(14, "20"), get(15, "20"), get(16, "20")
        thrR_minus, thrG_minus, thrB_minus = get(17, "20"), get(18, "20"), get(19, "20")
        math_formula = get(20, "")

        # Formül sekmesi alanları
        if hasattr(self, "formul_name_input"): self.formul_name_input.setText(name)
        self.formul_motor1_input.setText(m1)
        self.formul_motor2_input.setText(m2)
        self.formul_motor3_input.setText(m3)
        self.formul_motor3_preload_input.setText(m3pre)
        if hasattr(self, "formul_motor4_input"): self.formul_motor4_input.setText(m4)
        if hasattr(self, "formul_motor5_input"): self.formul_motor5_input.setText(m5)

        if hasattr(self, "formul_air_pump_input_2"):
            self.formul_air_pump_input_2.setText(air)
        elif hasattr(self, "formul_air_pump_input"):
            self.formul_air_pump_input.setText(air)

        if hasattr(self, "formul_water_pump_input_2"):
            self.formul_water_pump_input_2.setText(water)
        elif hasattr(self, "formul_water_pump_input"):
            self.formul_water_pump_input.setText(water)

        if hasattr(self, "formul_selenoid_valve_input_2"):
            self.formul_selenoid_valve_input_2.setText(selen)
        elif hasattr(self, "formul_selenoid_valve_input"):
            self.formul_selenoid_valve_input.setText(selen)

        self.formul_cokme_valve_input.setText(cokme)

        # Hedef & eşikler
        self.formul_target_input_R.setText(R)
        self.formul_target_input_G.setText(G)
        self.formul_target_input_B.setText(B)
        self.formul_threshold_input_R.setText(thrR_plus)
        self.formul_threshold_input_G.setText(thrG_plus)
        self.formul_threshold_input_B.setText(thrB_plus)
        if hasattr(self, "formul_threshold_input_R_2"): self.formul_threshold_input_R_2.setText(thrR_minus)
        if hasattr(self, "formul_threshold_input_G_2"): self.formul_threshold_input_G_2.setText(thrG_minus)
        if hasattr(self, "formul_threshold_input_B_2"): self.formul_threshold_input_B_2.setText(thrB_minus)

        # Test ekranını da senkronla (kayma bitsin)
        if hasattr(self, "sample_input"):    self.sample_input.setText(m1)
        if hasattr(self, "indicator_input"): self.indicator_input.setText(m2)
        if hasattr(self, "titrant_input"):   self.titrant_input.setText(m3)
        if hasattr(self, "target_input_R"): self.target_input_R.setText(R)
        if hasattr(self, "target_input_G"): self.target_input_G.setText(G)
        if hasattr(self, "target_input_B"): self.target_input_B.setText(B)
        self.formul_air_pump_time       = fnum(air,   5)
        self.formul_water_pump_time     = fnum(water, 3)
        self.formul_selenoid_valve_time = fnum(selen, 3)
        self.formul_cokme_valve_time    = fnum(cokme, 10)

        # Math formülünü ekrana aktar
        if hasattr(self, "math_formul_input"):
            self.math_formul_input.setText(math_formula)

    # ---------- Dev/IO ----------
    def control_motor1(self, ml_value):
        self._motor_cmd(1, ml_value)

    def control_motor2(self, ml_value):
        self._motor_cmd(2, ml_value)

    def control_motor3(self, ml_value):
        self._motor_cmd(3, ml_value)

    def _motor_cmd(self, idx, ml_value):
        try:
            val = float(str(ml_value).replace(',', '.'))
            steps = int(val * self.motor_resolution[f"motor{idx}"])
            if self.worker:
                self.worker.send_command(f"MOVE{idx} {steps}", "DONE", 15.0)
        except Exception:
            pass

    # POMPALAR / VALF (GERÇEK TOGGLE)
    def toggle_air_pump(self):
        if not self.worker:
            return
        cmd = "AIR_OFF" if self.air_on else "AIR_ON"
        res = self.worker.send_command(cmd, "DONE", 2.0)
        if res and not str(res).upper().startswith("ERR"):
            self.air_on = not self.air_on
            self.set_status(f"Air {'ON' if self.air_on else 'OFF'}")

    def trigger_air_pump(self, duration):
        if not self.worker:
            return
        try:
            d = float(str(duration).replace(',', '.'))
            self.worker.send_command(f"AIR_DUR {int(d*1000)}", "DONE", d + 2)
        except Exception:
            pass

    def toggle_water_pump(self):
        if not self.worker:
            return
        cmd = "WATER_OFF" if self.water_on else "WATER_ON"
        res = self.worker.send_command(cmd, "DONE", 2.0)
        if res and not str(res).upper().startswith("ERR"):
            self.water_on = not self.water_on
            self.set_status(f"Water {'ON' if self.water_on else 'OFF'}")

    def trigger_water_pump(self, duration):
        if not self.worker:
            return
        try:
            d = float(str(duration).replace(',', '.'))
            self.worker.send_command(f"WATER_DUR {int(d*1000)}", "DONE", d + 2)
        except Exception:
            pass

    def toggle_selenoid_valve(self):
        if not self.worker:
            return
        cmd = "VALVE_OFF" if self.valve_on else "VALVE_ON"
        res = self.worker.send_command(cmd, "DONE", 2.0)
        if res and not str(res).upper().startswith("ERR"):
            self.valve_on = not self.valve_on
            self.set_status(f"Valve {'ON' if self.valve_on else 'OFF'}")

    def trigger_selenoid_valve(self, duration):
        if not self.worker:
            return
        try:
            d = float(str(duration).replace(',', '.'))
            self.worker.send_command(f"VALVE_DUR {int(d*1000)}", "DONE", d + 2)
        except Exception:
            pass

    # ---------- Yoğunluk / pH ----------
    def get_weight(self):
        if not self.worker:
            return None
        line = self.worker.send_command("WEIGHT_MEASURE", "WEIGHT:", 5.0)
        if line and startswith_token(line, "WEIGHT:"):
            try:
                val = float(line.split(":", 1)[1])
                sc = QGraphicsScene(); sc.addText(f"{val:.2f} gram")
                self.weight_output.setScene(sc)
                return val
            except Exception:
                pass
        self.set_status("Ağırlık alınamadı.")
        return None

    def calculate_density(self):
        try:
            w0 = self.get_weight()
            if w0 is None:
                return
            sel = self.motor_combobox.currentText()
            vol = float(self.volume_input.text().replace(',', '.'))
            if vol <= 0:
                sc = QGraphicsScene(); sc.addText("Error: Volume > 0 olmalı.")
                self.calculate_output.setScene(sc)
                return
            if sel == "Motor1":
                self.control_motor1(vol)
            elif sel == "Motor2":
                self.control_motor2(vol)
            else:
                self.control_motor3(vol)
            w1 = self.get_weight()
            if w1 is None:
                return
            dens = (w1 - w0) / vol
            sc = QGraphicsScene(); sc.addText(f"{dens:.2f} g/ml")
            self.calculate_output.setScene(sc)
        except Exception:
            sc = QGraphicsScene(); sc.addText("Failed to retrieve weight or volume.")
            self.calculate_output.setScene(sc)

    def get_ph(self):
        if not self.worker:
            return None
        line = self.worker.send_command("PH_MEASURE", "PH:", 5.0)
        if line and startswith_token(line, "PH:"):
            try:
                val = float(line.split(":", 1)[1])
                sc = QGraphicsScene(); sc.addText(f"pH: {val:.2f}")
                self.ph_output.setScene(sc)
                return val
            except Exception:
                pass
        self.set_status("pH alınamadı.")
        return None

    # ---------- Mat. Formül ----------
    def calculate_math_formul(self):
        try:
            t = float(self.sample_input.text() or 0)
            if t <= 0:
                self._set_scene_text("math_formul_output", "Örnek hacmi (t) > 0 olmalı.")
                return None
            S = self.calculate_titrant_total()
            N = float(self.math_constants.get("N", 1))
            F = float(self.math_constants.get("F", 1))
            result = (S * F * N * 3) / t
            self._set_scene_text("math_formul_output", f"% CH2O = {result:.2f}")
            return result
        except Exception as e:
            self._set_scene_text("math_formul_output", f"Hata: {e}")
            return None

    def calculate_titrant_total(self):
        try:
            preload = float(self.formul_motor3_preload_input.text() or 0)
            motor3_val = float(self.titrant_input.text() or 0)
            return preload + (motor3_val * self.successful_tests_count)
        except Exception:
            return 0

    # ---------- Yardımcı ----------
    def clear_rgb_lcds(self):
        for name in ("lcdNumber_Pointer_R","lcdNumber_Pointer_G","lcdNumber_Pointer_B",
                     "lcdNumber_Pointer_R_Dev","lcdNumber_Pointer_G_Dev","lcdNumber_Pointer_B_Dev"):
            if hasattr(self, name):
                getattr(self, name).display("")

    def set_status(self, txt: str):
        if hasattr(self, "status_label"):
            self.status_label.setText(txt)

    def get_current_rgb(self):
        return self.current_rgb if self.current_rgb else (0, 0, 0)

    def read_target_rgb(self):
        try:
            r = int(self.formul_target_input_R.text())
            g = int(self.formul_target_input_G.text())
            b = int(self.formul_target_input_B.text())
            return r, g, b
        except Exception:
            self.set_status("Hatalı RGB hedef.")
            return None


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("", 10))
    app.aboutToQuit.connect(MyApp.clean_exit)
    w = MyApp()
    w.show()
    sys.exit(app.exec_())
