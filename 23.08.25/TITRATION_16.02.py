from PyQt5.QtCore import pyqtSignal
import sys
import socket
import serial
import time
import datetime
import os
import serial.tools.list_ports
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject, QRunnable, QThreadPool
from PyQt5.QtWidgets import QApplication, QMainWindow, QGraphicsScene, QGraphicsView
from PyQt5.QtGui import QImage, QPixmap
from PyQt5 import uic 
from PyQt5 import QtWidgets
from PyQt5.QtCore import QEventLoop
from picamera2.picamera2 import Picamera2, libcamera
from PyQt5.QtWidgets import QApplication, QMainWindow, QPushButton
from PyQt5.QtCore import pyqtSlot 

class SerialWorker(QRunnable):
    def __init__(self, ser, command, callback=None):
        super().__init__()
        self.ser = ser
        self.command = command
        self.callback = callback

    def run(self):
        if self.ser:
            self.ser.write(self.command.encode())
            start_time = time.time()  # Döngünün başlama zamanı
            timeout = 60  # 5 saniye zaman aşımı
            while True:
                if self.ser.in_waiting:
                    line = self.ser.readline().decode().strip()
                    if line == "DONE":
                        break
                    elif line != "":
                        print("Received from Arduino:", line)
                    time.sleep(0.1)
                # Eğer zaman aşımı dolmuşsa döngüden çık
                if time.time() - start_time > timeout:
                    print("Timeout while waiting for Arduino response")
                    break
            if self.callback:
                self.callback()


class CameraThread(QThread):
    update_image = pyqtSignal(QImage, bytes)  

    def __init__(self):
        super().__init__()
        try:
            camera_info = Picamera2.global_camera_info()
            print(f"Kamera bilgisi: {camera_info}") 
            if len(camera_info) == 0:
                raise RuntimeError("Kamera tespit edilmedi.")
            self.picam2 = Picamera2()
            config = self.picam2.create_still_configuration(main={"size": (320, 240)})
            config["transform"] = libcamera.Transform(hflip=True, vflip=True)
            self.picam2.configure(config)
            print("Kamera başarıyla başlatıldı.")
        except IndexError:
            print("Belirtilen indeksle eşleşen kamera bulunamadı.")
            self.picam2 = None
        except Exception as e:
            print(f"Kamera başlatılırken hata oluştu: {e}")
            self.picam2 = None

    def run(self):
        if self.picam2:
            try:
                self.picam2.start()
                print("Kamera çalıştırıldı.")
                while True:
                    image, raw_data = self.capture_image()
                    if image is not None and raw_data is not None:
                        self.update_image.emit(image, raw_data)
                    else:
                        print("Görüntü yakalanamadı.")
            except Exception as e:
                print(f"Kamera çalışırken hata oluştu: {e}")
        else:
            print("Kamera başlatılamadı.")

    def capture_image(self):
        try:
            frame = self.picam2.capture_array()

            raw_data = frame.tobytes()
            height, width, channel = frame.shape
            bytes_per_line = 3 * width
            qImg = QImage(frame.data, width, height, bytes_per_line, QImage.Format_RGB888)
            return qImg, raw_data
        except Exception as e:
            print(f"Görüntü yakalanırken hata oluştu: {e}")
            return None, None


class TcpClientThread(QThread):
    
    data_received = pyqtSignal(str)
    connection_error = pyqtSignal(str)

    def __init__(self, server_ip, server_port):
        super(TcpClientThread, self).__init__() #super().init()
        self.server_ip = server_ip
        self.server_port = server_port
        self.running = True

    def run(self):
        print("TcpClientThread başlatıldı.")
        while self.running:
            try:
                print(f"Sunucuya bağlanıyor: IP={self.server_ip}, Port={self.server_port}")
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
                    client_socket.connect((self.server_ip, self.server_port))
                    print("Bağlantı başarılı.")
                    
                    while self.running:
                        response = client_socket.recv(4096)
                        if response:
                            print("Veri alındı:", response.decode())
                            self.data_received.emit(response.decode())
                        else:
                            raise ConnectionError("Sunucudan veri alınamadı.")
            except Exception as e:
                error_message = f"Bağlantı hatası: {e}"
                print(error_message)
                self.connection_error.emit(error_message)
                time.sleep(5)  # Yeniden denemeden önce 5 saniye bekle


class MyApp(QMainWindow):
    class PhWorker(QRunnable):
        def __init__(self, parent, port_name, selected_motor, motor_value):
            super().__init__()
            self.parent = parent
            self.port_name = port_name
            self.selected_motor = selected_motor
            self.motor_value = motor_value

        def run(self):
            import serial, time
            ser_ph = serial.Serial(self.port_name, 9600, timeout=1)
            time.sleep(1)
            ser_ph.reset_input_buffer()
            # Motoru çalıştır
            if self.selected_motor == "Motor1":
                self.parent.control_motor1(self.motor_value)
            elif self.selected_motor == "Motor2":
                self.parent.control_motor2(self.motor_value)
            elif self.selected_motor == "Motor3":
                self.parent.control_motor3(self.motor_value)
            # Motor işlemi bitene kadar Arduino'dan "DONE" mesajını bekle
            while True:
                done_message = ser_ph.readline().decode('utf-8').strip()
                if done_message == "DONE":
                    break
            # Motor durduktan sonra PH ölçümü komutunu gönder ve sürekli oku
            ser_ph.write(b"PH_MEASURE\n")
            time.sleep(0.5)
            # PH verisini sürekli oku ve ekrana yaz
            while True:
                ph_data = ser_ph.readline().decode('utf-8').strip()
                if ph_data.startswith("PH:"):
                    try:
                        ph_value = float(ph_data.split(": ")[1])
                        self.parent.update_ph_output(ph_value)
                    except Exception as e:
                        print(f"pH verisi hatalı: {ph_data}, hata: {e}")
                # Döngüyü durdurmak için bir mekanizma eklenebilir
                time.sleep(0.5)
            ser_ph.close()
    def update_ph_output(self, ph_value):
        scene = QGraphicsScene()
        scene.addText(f"pH: {ph_value:.2f}")
        self.ph_output.setScene(scene)
    def __init__(self):
        super().__init__()                                                                                                                                                                                                                  

        self.thread_pool = QThreadPool()  # Thread pool oluşturuldu
        self.ser = None
        self.select_com_port()
        uic.loadUi('frontend_v8.ui', self)

        self.rgb_received = False

        self.motor3_working = False
        self.processing_camera_data = False
        self.last_camera_process_time = 0

        self.current_rgb = None
        self.formul_air_pump_time = None
        self.formul_water_pump_time = None
        self.formul_selenoid_valve_time = None
        self.formul_cokme_valve_time = None
        
        # Math formül değişkenleri
        self.math_formul = ""
        self.math_constants = {
            "N": 1,  # Normalite
            "F": 1,  # Faktör
            "3": 3   # Sabit değer
        }
        
        self.loadFormulas()
        
        self.successful_tests_count = 0
        self.test_in_progress = False 
        self.current_image_data = None
        self.motor_units = {"motor1": "ml", "motor2": "ml", "motor3": "ml"}
        self.motor_resolution = {"motor1": 2777, "motor2": 2777, "motor3": 2777}    

        self.motor3_preload_done = False

        self.scene = QGraphicsScene(self)
        self.graphics_view_1 = self.findChild(QGraphicsView, 'graphicsView_1')
        self.graphics_view_2 = self.findChild(QGraphicsView, 'graphicsView_2')
        self.graphics_view_3 = self.findChild(QGraphicsView, 'graphicsView_3')

        self.graphics_view_1.setScene(self.scene)
        self.graphics_view_2.setScene(self.scene)
        self.graphics_view_3.setScene(self.scene)

        # Kamera ve TCP threadlerini tanımlayın
        self.camera_thread = CameraThread()
        self.camera_thread.update_image.connect(self.update_graphics_view)

        self.tcp_thread = TcpClientThread('192.158.56.1', 9876)

        self.air_pump_status = False
        self.selenoid_valve_status = False
        self.water_pump_status = False

        # Signal bağlantılarını kurun
        self.setup_signals()

        # Tcp ve kamera threadlerini başlatın
        self.tcp_thread.start()
        self.camera_thread.start()

        self.pushButton.clicked.connect(lambda: self.stackedWidget.setCurrentIndex(1))
        self.pushButton_2.clicked.connect(lambda: self.stackedWidget.setCurrentIndex(4))
        self.pushButton_3.clicked.connect(lambda: self.stackedWidget.setCurrentIndex(3))
        self.pushButton_4.clicked.connect(lambda: self.stackedWidget.setCurrentIndex(2))

    @staticmethod
    def clean_exit():
        QThreadPool.globalInstance().clear()
        
    def closeEvent(self, event):
        self.clean_exit_signal = True
        QThreadPool.globalInstance().waitForDone()
        event.accept()

    def select_com_port(self):
        ports = serial.tools.list_ports.comports()
        port_list = [f"{port.device} - {port.description}" for port in ports]
        port, ok = QtWidgets.QInputDialog.getItem(self, "Select COM Port", "Available COM Ports:", port_list, 0, False)
        if ok and port:
            port_name = port.split(' - ')[0]
            self.ser = serial.Serial(port_name, 9600, timeout=1)
            print(f"COM connected: {port_name}")
        else:
            print("No port selected, using default /dev/ttyUSB0")
            self.ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)
    def setup_signals(self):
        # Kamera ve TCP sinyalleri
        self.camera_thread.update_image.connect(self.update_graphics_view)
        # self.tcp_thread.data_received.connect(self.process_camera_data)
        self.tcp_thread.connection_error.connect(self.handle_connection_error)

        #ÖLÇÜM PAGE
        self.formula_combobox.currentIndexChanged.connect(self.loadFormula)
        self.preProcess_button.clicked.connect(self.preprocess)
        self.start_test_button.clicked.connect(self.start_test)
        self.complete_button.clicked.connect(self.complete_test)
        self.report_button.clicked.connect(self.save_report)
        self.clean_button.clicked.connect(self.clean_system)

        #DEV PAGE
        self.dev_motor1_button.clicked.connect(lambda: self.control_motor1(self.dev_motor1_input.text()))
        self.dev_motor2_button.clicked.connect(lambda: self.control_motor2(self.dev_motor2_input.text()))
        self.dev_motor3_button.clicked.connect(lambda: self.control_motor3(self.dev_motor3_input.text()))
        self.dev_air_pump_onoff.clicked.connect(lambda: self.toggle_air_pump())
        self.dev_water_pump_onoff.clicked.connect(lambda: self.toggle_water_pump())
        self.dev_selenoid_valve_onoff.clicked.connect(lambda: self.toggle_selenoid_valve())
        self.dev_air_pump_button.clicked.connect(lambda: self.trigger_air_pump(self.dev_air_pump_input.text()))
        self.dev_water_pump_button.clicked.connect(lambda: self.trigger_water_pump(self.dev_water_pump_input.text()))
        self.dev_selenoid_valve_button.clicked.connect(lambda: self.trigger_selenoid_valve(self.dev_selenoid_valve_input.text()))
        self.dev_camera_button.clicked.connect(lambda: self.control_camera())

        # FORMUL PAGE (Math Formül Tabı)
        self.save_formul_button.clicked.connect(self.saveFormula)
        self.formul_load_button.clicked.connect(self.loadFormula)

        #DENSITY PAGE
        self.weight_button.clicked.connect(self.get_weight)
        self.calculate_button.clicked.connect(self.calculate_density)
        self.ph_button.clicked.connect(self.get_ph)

    def update_graphics_view(self, qImg, raw_data): 
        self.scene.clear()
        self.scene.addPixmap(QPixmap.fromImage(qImg.rgbSwapped()))
        self.graphics_view_1.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio) 
        self.graphics_view_2.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio) 
        self.graphics_view_3.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)
        self.current_image_data = raw_data   

    #Ölçüm
    def preprocess(self):
        self.status_label.setText("Hazırlık")
        ml1 = 38000
        ml2 = 38000
        ml3 = 38000
        self.ser.write(f'MOVE1 {ml1}\n'.encode())
        self.ser.write(f'MOVE2 {ml2}\n'.encode())
        self.ser.write(f'MOVE3 {ml3}\n'.encode())
        # self.clean_system()

    def start_test(self):
        self.test_in_progress = True
        self.current_rgb = None  # RGB'yi sıfırla
        self.rgb_received = False

        self.lcdNumber_Pointer_R.display("")
        self.lcdNumber_Pointer_G.display("")
        self.lcdNumber_Pointer_B.display("")
        self.lcdNumber_Pointer_R_Dev.display("")
        self.lcdNumber_Pointer_G_Dev.display("")
        self.lcdNumber_Pointer_B_Dev.display("")

        print("RGB sıfırlandı ve test başlatıldı.")

        if self.read_target_rgb():
            self.status_label.setText("Test başlatıldı")
            try:
                # Motor 1 ve Motor 2 değerlerini al
                ml1_float = float(self.sample_input.text().replace(',', '.'))
                ml2_float = float(self.indicator_input.text().replace(',', '.'))

                # Motor 1 ve 2'yi sırayla çalıştır
                self.control_motor1(ml1_float)
                self.control_motor2(ml2_float)

                # Motor işlemleri tamamlandıktan sonra işlemleri devam ettir
                self.repeat_actions()

            except ValueError:
                self.status_label.setText("Geçersiz giriş: Lütfen sayısal değer girin.")
        else:
            self.status_label.setText("RGB değerleri okunamıyor.")

    def repeat_actions(self):
        if not self.test_in_progress or self.motor3_working:
            return  # Test tamamlandıysa işlemi durdur

        print("Repeat Actions - Air Pump Time:", self.formul_air_pump_time)
        print("Repeat Actions - Water Pump Time:", self.formul_water_pump_time)
        print("Repeat Actions - Selenoid Valve Time:", self.formul_selenoid_valve_time)
        print("Repeat Actions - Çökme Valve Time:", self.formul_cokme_valve_time)


        if not self.motor3_preload_done:
            preload_value = self.formul_motor3_preload_input.text()
            if preload_value and float(preload_value) > 0:
                preload_ml = float(preload_value.replace(',', '.'))
                self.control_motor3(preload_ml)
                print(f"Motor 3 önyükleme yapıldı: {preload_ml} ml")
                self.motor3_preload_done = True  # Flag'i ayarla

        # 1. Motor 3'ü çalıştır
        self.motor3_working = True
        ml3_float = float(self.titrant_input.text().replace(',', '.'))

        # Motor 3'ü başlat ve tamamlanınca after_motor3 fonksiyonunu çağır
        self.control_motor3(ml3_float)
        print(f"Motor 3 çalıştırıldı: {ml3_float} ml")

        # Motor işlemi tamamlanınca after_motor3 fonksiyonunu çağır
        QTimer.singleShot(3000, self.after_motor3)  # Motor 3 işlemi için gecikme

    def after_motor3(self):
        # Motor 3'ün bitmesini bekle
        if not self.motor3_working:
            return
        # 2. Hava pompasını çalıştır ve tamamlanınca after_air_pump_done fonksiyonunu çağır
        air_pump_time = self.formul_air_pump_time if self.formul_air_pump_time else 5
        self.trigger_air_pump(air_pump_time)
        print(f"Hava pompası açıldı ({air_pump_time} saniye)")
        
        # Hava pompası süresi tamamlandığında after_air_pump_done fonksiyonunu çağır
        QTimer.singleShot(int(air_pump_time * 1000), self.after_air_pump_done)  # Hava pompası işlemi için gecikme

    def after_air_pump_done(self):
        # Hava pompasını kapat
        self.air_pump_status = False
        print("Hava pompası kapandı")
        
        # Çökme süresini Arduino'ya gönder
        try:
            if self.formul_cokme_valve_time is None:
                # Eğer çökme süresi tanımlanmamışsa varsayılan süreyi kullan
                cokme_time = 3000  # Varsayılan süre 3000ms (3 saniye)
            elif isinstance(self.formul_cokme_valve_time, str):
                # Eğer çökme süresi bir string ise, virgülü noktaya çevirip hesapla
                cokme_time = int(float(self.formul_cokme_valve_time.replace(',', '.')) * 1000)
            else:
                # Eğer çökme süresi float veya int ise direkt olarak çarpma işlemi yap
                cokme_time = int(self.formul_cokme_valve_time * 1000)
        except ValueError:
            cokme_time = 3000  # Hatalı bir değer varsa varsayılan süreyi kullan
            print("Geçersiz çökme süresi, varsayılan süre kullanılıyor (3 saniye).")
        
        # Arduino'ya çökme süresi komutunu gönder
        command = f"COKME_DUR {cokme_time}\n"
        self.ser.write(command.encode())
        print(f"Çökme süresi gönderildi: {cokme_time / 1000} saniye")
        
        # Çökme süresini Arduino yönetirken, doğrudan kamerayı tetikle
        QTimer.singleShot(cokme_time, self.trigger_camera)

    def trigger_camera(self):
        # Kamerayı tetikle
        self.control_camera()
        print("Kamera tetikleniyor")
        
        # Kamera tetiklendiğinde RGB verisinin işlenmesini bekleyin
        self.tcp_thread.data_received.connect(self.process_camera_data)

    def camera_triggered(self):
        print("Kamera tetiklendi")
        # RGB verisinin işlenmesini bekleyin
        self.tcp_thread.data_received.connect(self.process_camera_data)

    def check_and_repeat_rgb(self):
        if not self.test_in_progress or not self.rgb_received:
            print("RGB verisi alınmadı veya test devam etmiyor, tekrar yok")  # Yeni print ifadesi
            return

        r, g, b = self.get_current_rgb()
        target_rgb = self.read_target_rgb()

        if target_rgb:
            target_r, target_g, target_b = target_rgb
            threshold_r = int(self.formul_threshold_input_R.text() or 20)
            threshold_g = int(self.formul_threshold_input_G.text() or 20)
            threshold_b = int(self.formul_threshold_input_B.text() or 20)

            # Hedef RGB'ye ulaşılıp ulaşılmadığını kontrol et
            if (target_r - threshold_r <= r <= target_r + threshold_r and
                target_g - threshold_g <= g <= target_g + threshold_g and
                target_b - threshold_b <= b <= target_b + threshold_b):
                print("Hedef RGB değerlerine ulaşıldı:", (r, g, b))  # Yeni print ifadesi
                self.status_label.setText("Hedef RGB değerlerine ulaşıldı.")
                self.complete_test()  # Testi tamamla
            else:
                print(f"RGB değerleri hedefte değil, işlem tekrarlanıyor. Mevcut RGB: ({r}, {g}, {b})")
                self.status_label.setText(f"RGB hedefte değil. Mevcut RGB: ({r}, {g}, {b})")
                self.motor3_working = False
                self.repeat_actions()

    def get_current_rgb(self):
        return self.current_rgb if self.current_rgb else (0, 0, 0)

    def read_target_rgb(self):
        try:
            r = int(self.target_input_R.text())
            g = int(self.target_input_G.text())
            b = int(self.target_input_B.text())
            return r, g, b
        except ValueError:
            self.status_label.setText("Hatalı RGB hedef değerleri.")
            return None

    def send_motor_commands(self, motor_values):
        for i, motor in enumerate(["motor1", "motor2", "motor3"], start=1):
            steps = int(motor_values[i-1] * self.motor_resolution[motor])
            self.ser.write(f'MOVE{i} {steps}\n'.encode())

    def complete_test(self):
        if self.test_in_progress:  
            self.test_in_progress = False
            self.motor3_working = False
            self.motor3_preload_done = False
            
            self.status_label.setText("Test tamamlandı.")
            
            # current_rgb'nin olup olmadığını kontrol et
            if self.current_rgb is not None:
                r, g, b = self.current_rgb
                self.save_report(r, g, b)  # Raporu kaydet
                
                # Formül sonucunu hesapla ve göster
                result = self.calculate_math_formul()
                if result is not None:
                    scene = QGraphicsScene()
                    scene.addText(f"Test Sonucu:\nRGB: ({r}, {g}, {b})\n% CH2O = {result:.2f}")
                    self.graphicsView_output.setScene(scene)
            else:
                self.status_label.setText("RGB değerleri mevcut değil, rapor kaydedilemedi.")
            
            self.ser.write('COMPLETE_TEST\n'.encode())
            if self.ser.in_waiting > 0:
                message = self.ser.readline().decode().strip()
                self.status_label.setText(f"Transfer count: {self.successful_tests_count}")
                
            time.sleep(0.5)

            self.successful_tests_count = 0

            self.current_rgb = None
            self.rgb_received = False

            print("RGB sıfırlandı: ", self.current_rgb, self.rgb_received)

    ##Temizlik
    def clean_system(self):
        self.status_label.setText("TEMİZLİK")

        try:
            valve_time = int(self.formul_selenoid_valve_time) * 1000 
            water_time = int(self.formul_water_pump_time) * 1000
            air_time = int(self.formul_air_pump_time) * 1000
        except ValueError:
            self.status_label.setText("Geçersiz giriş, varsayılan süreler kullanılıyor.")
            valve_time = 3000
            water_time = 3000
            air_time = 5000

        self.ser.write("VALVE_ON\n".encode())
        print("VALVE_ON")

        QTimer.singleShot(valve_time, lambda: (
            self.ser.write("VALVE_OFF\n".encode()),
            QTimer.singleShot(1000, lambda: (self.ser.write("AIR_ON\n".encode()), self.ser.write("WATER_ON\n".encode())))))
        print("VALVE_OFF -> AIR_ON ve WATER_ON")

        QTimer.singleShot(valve_time + water_time + 1000, lambda: self.ser.write("WATER_OFF\n".encode()))
        print("WATER_OFF")

        QTimer.singleShot(valve_time + water_time + 1500, lambda: self.ser.write("VALVE_ON\n".encode()))
        print("VALVE_ON (Tekrar)")

        QTimer.singleShot(valve_time + air_time + water_time + 2000, lambda: (
            self.ser.write("AIR_OFF\n".encode()), self.ser.write("VALVE_OFF\n".encode())))
        print("AIR_OFF ve VALVE_OFF")
##kamera
    def process_camera_data(self, data):

        print("Kameradan veri alındı:", data)
        current_time = time.time()
        if current_time - self.last_camera_process_time < 3:  # En az 3 saniye bekleme
            print("Veri çok hızlı alındı, atlanıyor")
            return

        self.last_camera_process_time = current_time  # Zamanı güncelle

        # RGB verilerini işle
        rgb_values = data.strip().split()
        print("Veri parçalandı:", rgb_values)  
        
        if len(rgb_values) == 3:
            try:
                r, g, b = map(float, rgb_values)
                r, g, b = int(r), int(g), int(b)
                print("Alınan RGB:", (r, g, b)) 
                
                self.lcdNumber_Pointer_R.display(r)
                self.lcdNumber_Pointer_G.display(g)
                self.lcdNumber_Pointer_B.display(b)

                self.lcdNumber_Pointer_R_Dev.display(r)
                self.lcdNumber_Pointer_G_Dev.display(g)
                self.lcdNumber_Pointer_B_Dev.display(b)

                QApplication.processEvents()


                self.current_rgb = (r, g, b)
                self.rgb_received = True

                # RGB başarıyla alındıktan sonra sinyali kes
                #self.tcp_thread.data_received.disconnect(self.process_camera_data)
                
                if self.test_in_progress:
                    self.successful_tests_count += 1
                    # scene = QGraphicsScene()
                    # scene.addText(f"Transfer count: {self.successful_tests_count}")
                    # self.result_label.setScene(scene)
                    self.status_label.setText(f"Transfer count: {self.successful_tests_count}")

                # RGB kontrolü yap ve tekrar gerekiyorsa işlemi tekrarla
                self.check_and_repeat_rgb()
            except ValueError:
                print("Hatalı RGB verisi")  # Yeni print ifadesi
                self.rgb_received = False
        else:
            print("RGB verisi eksik veya hatalı:", data) 
## rapor kaydet
    def save_report(self, r=None, g=None, b=None):
        if self.current_rgb:
            r, g, b = self.current_rgb  # RGB değerlerini al

        if r is None or g is None or b is None:
            # Eğer RGB değerleri mevcut değilse, raporu kaydetme
            self.status_label.setText("RGB değerleri eksik, rapor kaydedilemiyor.")
            return

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report_dir = "reports"
        os.makedirs(report_dir, exist_ok=True)
        date_dir = os.path.join(report_dir, now.split(" ")[0])
        os.makedirs(date_dir, exist_ok=True)

        report_filename = os.path.join(date_dir, "report.txt")
        with open(report_filename, "a") as file:
            file.write(f"{now}, RGB: ({r}, {g}, {b})\n")
        
        self.status_label.setText("Test Tamamlandı, Rapor başarıyla kaydedildi.")


    def handle_connection_error(self, error):
        self.status_label.setText(f"Bağlantı hatası: {error}")

### Formül
    def saveFormula(self):
        formula_name = self.formul_name_input.text()
        formula_data = [
            formula_name,
            self.formul_motor1_input.text(),
            self.formul_motor2_input.text(),
            self.formul_motor3_input.text(),
            self.formul_motor3_preload_input.text(),
            self.formul_air_pump_input.text(),  # Main formül hava
            self.formul_cokme_valve_input.text(),
            self.formul_target_input_R.text(),
            self.formul_target_input_G.text(),
            self.formul_target_input_B.text(),
            self.formul_threshold_input_R.text(),
            self.formul_threshold_input_G.text(),
            self.formul_threshold_input_B.text(),
            #
            self.formul_air_pump_input_2.text(),  # Temizlik hava
            self.formul_water_pump_input_2.text(),
            self.formul_selenoid_valve_input_2.text(),
            #
            self.math_formul_input.text()  # Math formül
        ]
        # Formül dosyasını oku, aynı isim varsa sil
        formulas = []
        try:
            with open('formulas.txt', 'r') as file:
                for line in file:
                    parts = line.strip().split(',')
                    if parts[0] != formula_name:
                        formulas.append(line.strip())
        except FileNotFoundError:
            pass
        # Yeni formülü ekle
        formulas.append(','.join(formula_data))
        with open('formulas.txt', 'w') as file:
            for f in formulas:
                file.write(f + '\n')
        # Combobox'a ekle (varsa tekrar eklemez)
        if self.formula_combobox.findText(formula_name) == -1:
            self.formula_combobox.addItem(formula_name)
        self.status_label.setText("Formül kaydedildi.")

    def loadFormula(self):
        selected_formula = self.formula_combobox.currentText()
        with open('formulas.txt', 'r') as file:
            for line in file:
                parts = line.strip().split(',')
                if parts[0] == selected_formula:
                    self.formul_name_input.setText(parts[0])
                    self.formul_motor1_input.setText(parts[1])
                    self.formul_motor2_input.setText(parts[2])
                    self.formul_motor3_input.setText(parts[3])
                    self.formul_motor3_preload_input.setText(parts[4])
                    self.formul_air_pump_input.setText(parts[5])
                    self.formul_cokme_valve_input.setText(parts[6])
                    self.formul_target_input_R.setText(parts[7])
                    self.formul_target_input_G.setText(parts[8])
                    self.formul_target_input_B.setText(parts[9])
                    self.formul_threshold_input_R.setText(parts[10])
                    self.formul_threshold_input_G.setText(parts[11])
                    self.formul_threshold_input_B.setText(parts[12])
                    self.formul_air_pump_input_2.setText(parts[13])
                    self.formul_water_pump_input_2.setText(parts[14])
                    self.formul_selenoid_valve_input_2.setText(parts[15])
                    self.math_formul_input.setText(parts[16])
                    self.apply_formula(parts)
                    break
        self.status_label.setText("Formül yüklendi.")

    def loadFormulas(self):
    #formulas.txt dosyasından formülleri yükler ve comboBox'a ekleme
        try:
            with open('formulas.txt', 'r') as file:
                for line in file:
                    parts = line.strip().split(',')
                    if parts and len(parts) > 0:  # Formül geçerli ise
                        self.formula_combobox.addItem(parts[0])
        except FileNotFoundError:
            print("Formulas.txt dosyası bulunamadı, henüz kaydedilmiş bir formül yok.")
        except Exception as e:
            print(f"Formülleri yüklerken bir hata oluştu: {e}")

    def apply_formula(self, formula_data):
        self.sample_input.setText(formula_data[1])
        self.indicator_input.setText(formula_data[2])
        self.titrant_input.setText(formula_data[3])
        self.formul_motor3_preload_input.setText(formula_data[4]) 

        self.target_input_R.setText(formula_data[9])
        self.target_input_G.setText(formula_data[10])
        self.target_input_B.setText(formula_data[11])

        self.formul_air_pump_time = float(formula_data[5].replace(',', '.')) if formula_data[5] else 5
        self.formul_water_pump_time = float(formula_data[6].replace(',', '.')) if formula_data[6] else 3
        self.formul_selenoid_valve_time = float(formula_data[7].replace(',', '.')) if formula_data[7] else 3
        self.formul_cokme_valve_time = float(formula_data[8].replace(',', '.')) if formula_data[8] else 10
        
        print("Air Pump Time:", self.formul_air_pump_time)
        print("Water Pump Time:", self.formul_water_pump_time)
        print("Selenoid Valve Time:", self.formul_selenoid_valve_time)
        print("Çökme Valve Time:", self.formul_cokme_valve_time)


    def setUnit(self, motor, unit):
        self.motor_units[motor] = unit
        self.update_unit_styles(motor, unit)

    def update_unit_styles(self, motor, unit):
        if unit == "gram":
            getattr(self, f'formul_{motor}_gram_button').setStyleSheet("background-color: green;")
            getattr(self, f'formul_{motor}_ml_button').setStyleSheet("")
        else:
            getattr(self, f'formul_{motor}_gram_button').setStyleSheet("")
            getattr(self, f'formul_{motor}_ml_button').setStyleSheet("background-color: green;")

    ## DENSITY
    def get_weight(self):
        if self.ser_weight is None or not self.ser_weight.is_open:
            print("Ağırlık için seri port bağlı değil veya açık değil.")
            return None
        time.sleep(1)

        self.ser_weight.reset_input_buffer()  
        self.ser_weight.write(b"WEIGHT_MEASURE\n")
        time.sleep(0.5)  # İlk bekleme
        attempt_count = 5  # Yanıtı almak için maksimum deneme sayısı

        for _ in range(attempt_count):
            weight_data = self.ser_weight.readline().decode('utf-8').strip()

            if weight_data.startswith("Weight:"):
                weight_value = float(weight_data.split(": ")[1])

                scene = QGraphicsScene()
                scene.addText(f"{weight_value:.2f} gram")
                self.weight_output.setScene(scene)

                print(f"Ağırlık alındı: {weight_value} gram")
                return weight_value 
            
            time.sleep(0.5)  
        print("Ağırlık bilgisi alınamadı.")
        return None


    def calculate_density(self):
        try:
            # İlk ağırlık ölçümü (tartım öncesi ağırlık)
            initial_weight = self.get_weight()
            if initial_weight is None:
                print("Başlangıç ağırlığı alınamadı.")
                return

            print(f"Başlangıç ağırlığı: {initial_weight} gram")  # İlk ağırlık yazdırılıyor

            selected_motor = self.motor_combobox.currentText()
            volume = float(self.volume_input.text())  # Hacim girişinden alınan değer
            
            # Hacim kontrolü
            if volume <= 0:
                scene = QGraphicsScene()
                scene.addText("Error: Volume must be greater than 0.")
                self.calculate_output.setScene(scene)
                return  # İşlem iptal

            if selected_motor == "Motor1":
                self.control_motor1(volume)
            elif selected_motor == "Motor2":
                self.control_motor2(volume)
            elif selected_motor == "Motor3":
                self.control_motor3(volume)

            # Motor işlemi bitene kadar Arduino'dan "DONE" mesajını bekle
            while True:
                done_message = self.ser_weight.readline().decode('utf-8').strip()
                if done_message == "DONE":
                    print("Motor işlemi tamamlandı.")
                    break  

            # Motor durduktan sonra ikinci ağırlık ölçümü
            final_weight = self.get_weight()
            if final_weight is None:
                print("Son ağırlık alınamadı.")
                return

            print(f"Son ağırlık: {final_weight} gram")  

            weight_diff = final_weight - initial_weight
            density = weight_diff / volume  
            
            scene = QGraphicsScene()  
            scene.addText(f"{density:.2f} g/ml")  
            self.calculate_output.setScene(scene)

        except ValueError:
            scene = QGraphicsScene()
            scene.addText("Failed to retrieve weight or volume.")
            self.calculate_output.setScene(scene)

    def get_ph(self):
        # pH ölçümü için ayrı port seçimi
        ports = serial.tools.list_ports.comports()
        port_list = [f"{port.device} - {port.description}" for port in ports]
        port_ph, ok_ph = QtWidgets.QInputDialog.getItem(self, "Select COM Port for pH", "Available COM Ports:", port_list, 0, False)
        if ok_ph and port_ph:
            port_name_ph = port_ph.split(' - ')[0]
        else:
            print("No port selected for pH, using default /dev/ttyACM0")
            port_name_ph = '/dev/ttyACM0'

        selected_motor = self.motor_combobox_2.currentText()
        motor_value = self.ph_motor_input.text()
        try:
            motor_value = float(motor_value.replace(',', '.')) if motor_value else 1
        except ValueError:
            motor_value = 1

        worker = self.PhWorker(self, port_name_ph, selected_motor, motor_value)
        QThreadPool.globalInstance().start(worker)
    
########
    #Dev
    def control_motor1(self, ml_value):
        try:
            if isinstance(ml_value, str):  # ml_value stringse çevir virgülü noktaya
                ml_value = float(ml_value.replace(',', '.'))
            steps =  int(ml_value * self.motor_resolution["motor1"])

            command = f"MOVE1 {steps}\n"
            self.ser.write(command.encode())
            print(f"Motor 1 activated for {ml_value} ml - equivalent to {steps} steps")
        except ValueError:
            print("Invalid value for ml_value:", ml_value)

    def control_motor2(self, ml_value):
        try:
            if isinstance(ml_value, str):
                ml_value = float(ml_value.replace(',', '.'))
            steps = int(ml_value * self.motor_resolution["motor2"]) 

            command = f"MOVE2 {steps}\n"
            self.ser.write(command.encode())
            print(f"Motor 2 activated for {ml_value} ml - equivalent to {steps} steps")
        except ValueError:
            print("Invalid value for ml_value:", ml_value)


    def control_motor3(self, ml_value):
        try:
            if isinstance(ml_value, str):
                ml_value = float(ml_value.replace(',', '.'))
            steps = int(ml_value * self.motor_resolution["motor3"]) 

            command = f"MOVE3 {steps}\n"
            self.ser.write(command.encode())
            print(f"Motor 3 activated for {ml_value} ml - equivalent to {steps} steps")
        except ValueError:
            print("Invalid value for ml_value:", ml_value)

## AIR PUMP
    def toggle_air_pump(self):
        if self.air_pump_status:
            self.ser.write("AIR_OFF\n".encode())
            print("Air pump turned OFF")
            self.air_pump_status = False
        else:
            self.ser.write("AIR_ON\n".encode())
            print("Air pump turned ON")
            self.air_pump_status = True
    
    def trigger_air_pump(self, duration):
        try:
            duration = float(str(duration).replace(',', '.'))
            command = f"AIR_DUR {int(duration * 1000)}\n"
            self.ser.write(command.encode())
            print(f"AIR pump triggered for {duration} seconds")
        except ValueError:
            print(f"Invalid value for air pump duration: {duration}")

## WATER PUMP
    def toggle_water_pump(self):
        if self.water_pump_status:
            self.ser.write("WATER_OFF\n".encode())
            print("Water pump turned OFF")
            self.water_pump_status = False
        else:
            self.ser.write("WATER_ON\n".encode())
            print("Water pump turned ON")
            self.water_pump_status = True

    def trigger_water_pump(self, duration):
        try:
            duration = float(str(duration).replace(',', '.'))
            command = f"WATER_DUR {int(duration * 1000)}\n"
            self.ser.write(command.encode())
            print(f"Water pump triggered for {duration} seconds")
        except ValueError:
            print(f"Invalid value for water pump duration: {duration}")

## SELENOID VALVE
    def toggle_selenoid_valve(self):
        if self.selenoid_valve_status:
            self.ser.write("VALVE_OFF\n".encode())
            print("selenoid valve turned OFF")
            self.selenoid_valve_status = False
        else:
            self.ser.write("VALVE_ON\n".encode())
            print("selenoid valve turned ON")
            self.selenoid_valve_status = True

    def trigger_selenoid_valve(self, duration):
        try:
            duration = float(str(duration).replace(',', '.'))
            command = f"VALVE_DUR {int(duration * 1000)}\n"
            self.ser.write(command.encode())
            print(f"Selenoid valve triggered for {duration} seconds")
        except ValueError:
            print(f"Invalid value for solenoid valve duration: {duration}")

## CAMERA
    def control_camera(self):
        try:
            self.tcp_thread.data_received.disconnect(self.process_camera_data)
            print("Eski TCP bağlantısı temizlendi.")
        except TypeError:
            print("Eski TCP bağlantısı zaten yoktu.")

        self.tcp_thread.data_received.connect(self.process_camera_data)
        print("Yeni TCP bağlantısı oluşturuldu.")

        self.ser.write("CAMERA_TRIG\n".encode())
        print("Kamera tetikleme komutu gönderildi.")

##########

    def calculate_math_formul(self):
        """Math formülü hesapla"""
        try:
            # Numune miktarını al
            t = float(self.sample_input.text() or 0)
            if t <= 0:
                print("Geçerli numune miktarı girilmemiş")
                return None

            # Total titrant miktarını hesapla
            S = self.calculate_titrant_total()

            # Sabit değerleri kullan
            N = self.math_constants["N"]
            F = self.math_constants["F"]
            
            # Formaldehit formülü için hesaplama
            result = (S * F * N * 3) / t
            
            # Math formül tabındaki sonucu göster
            scene = QGraphicsScene()
            scene.addText(f"% CH2O = {result:.2f}")
            self.math_formul_output.setScene(scene)
            
            return result
            
        except Exception as e:
            print(f"Formül hesaplanırken hata oluştu: {e}")
            scene = QGraphicsScene()
            scene.addText(f"Hata: {str(e)}")
            self.math_formul_output.setScene(scene)
            return None

    def save_math_formul(self):
        """Math formülü kaydet"""
        formul = self.math_formul_input.text()
        if formul:
            self.math_formul = formul
            try:
                with open('math_formulas.txt', 'a') as file:
                    file.write(f"{formul}\n")
                print("Math formül kaydedildi:", formul)
                
                # Formülü hemen hesapla ve göster
                result = self.calculate_math_formul()
                if result is not None:
                    scene = QGraphicsScene()
                    scene.addText(f"Formül kaydedildi ve hesaplandı:\n% CH2O = {result:.2f}")
                    self.graphicsView_output.setScene(scene)
                    
            except Exception as e:
                print(f"Math formül kaydedilirken hata oluştu: {e}")

    def load_math_formul(self):
        """Kaydedilmiş math formülü yükle"""
        try:
            with open('math_formulas.txt', 'r') as file:
                formulas = file.readlines()
                if formulas:
                    last_formula = formulas[-1].strip()
                    self.math_formul_input.setText(last_formula)
                    self.math_formul = last_formula
                    print("Math formül yüklendi:", last_formula)
                    
                    # Formülü hemen hesapla ve göster
                    result = self.calculate_math_formul()
                    if result is not None:
                        scene = QGraphicsScene()
                        scene.addText(f"Formül yüklendi ve hesaplandı:\n% CH2O = {result:.2f}")
                        self.graphicsView_output.setScene(scene)
                        
        except FileNotFoundError:
            print("Henüz kaydedilmiş math formül yok.")
            scene = QGraphicsScene()
            scene.addText("Henüz kaydedilmiş formül yok.")
            self.graphicsView_output.setScene(scene)
        except Exception as e:
            print(f"Math formül yüklenirken hata oluştu: {e}")
            scene = QGraphicsScene()
            scene.addText(f"Hata: {str(e)}")
            self.graphicsView_output.setScene(scene)

    def calculate_titrant_total(self):
        """Total titrant miktarını hesapla (S değeri)"""
        try:
            preload = float(self.formul_motor3_preload_input.text() or 0)
            motor3_value = float(self.titrant_input.text() or 0)
            transfer_count = self.successful_tests_count
            S = preload + (motor3_value * transfer_count)
            return S
        except ValueError:
            print("Titrant hesaplaması için geçerli değerler girilmemiş")
            return 0

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.aboutToQuit.connect(MyApp.clean_exit)
    window = MyApp()
    window.show()
    sys.exit(app.exec_())