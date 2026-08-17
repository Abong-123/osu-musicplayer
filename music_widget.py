# -*- coding: utf-8 -*-
"""
osu!-style Desktop Music Widget
================================
Install dulu:

    pip install PyQt5==5.15.9 numpy

Cara pakai:
    1. Ganti MUSIC_FOLDER di bawah sesuai folder lagu kamu.
    2. Jalankan: python osu_music_widget.py
    3. Klik-drag panel kontrol (pojok kanan atas) untuk pindah posisi.
    4. F9  -> toggle "always on top" untuk panel kontrol.
    5. Esc / Ctrl+Q -> keluar.

"""

import sys
import os

# Paksa backend Windows Media Foundation, bukan DirectShow.
# WMF native dukung lebih banyak codec modern (AAC/M4A, dll) dan lebih
# jarang gagal render dengan error VFW_E_CANNOT_RENDER (0x80040266).
# HARUS di-set SEBELUM QApplication dibuat / modul QtMultimedia diimport.
os.environ.setdefault("QT_MULTIMEDIA_PREFERRED_PLUGINS", "windowsmediafoundation")

import random
import math
import time
from pathlib import Path

import numpy as np

from PyQt5.QtWidgets import (
    QWidget, QApplication, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSlider, QShortcut, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, QTime, QPoint, QRect, QUrl, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QPixmap, QKeySequence
from PyQt5.QtMultimedia import (
    QMediaPlayer, QMediaContent, QAudioProbe, QAudioFormat
)

# ===================== KONFIGURASI =====================
MUSIC_FOLDER = r"C:\Users\[users]\Music"  # <-- GANTI SESUAI FOLDER LAGU KAMU
SUPPORTED_EXT = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".wma")

# Gaya osu!: lingkaran tengah pink/magenta, bar tipis biru mengitari
LOGO_FILL_COLOR = QColor(230, 55, 130)     # isi lingkaran (pink)
RING_COLOR = QColor(255, 255, 255)         # cincin putih tebal
BAR_COLOR = QColor(70, 175, 255)           # warna bar spektrum (biru)
ACCENT_COLOR = LOGO_FILL_COLOR             # dipakai untuk beat-glow
BAR_COUNT = 140
# =======================================================


# ------------------------------------------------------------------
# VISUALIZER
# ------------------------------------------------------------------
class VisualizerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(600, 600)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnBottomHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.logo_radius = 195          # sedikit diperbesar dari 185
        self.ring_thickness = 10
        self.bar_start = self.logo_radius + self.ring_thickness + 4
        self.bar_count = BAR_COUNT
        self.half_bar_count = BAR_COUNT // 2   # <-- baru: jumlah bin unik (kiri==kanan)
        self.bar_min = 4
        self.bar_max = 95
        self.bar_heights = [self.bar_min] * self.bar_count
        self.target_heights = [self.bar_min] * self.bar_count

        self.deco_ring_1 = self.bar_start + self.bar_max + 25
        self.deco_ring_2 = self.deco_ring_1 + 30

        self.logo_image = None
        self.logo_text = "MUSIC"
        self.use_custom_logo = False

        self.fill_color = QColor(LOGO_FILL_COLOR)
        self.ring_color = QColor(RING_COLOR)
        self.bar_color = QColor(BAR_COLOR)
        self.current_glow = QColor(ACCENT_COLOR)

        self.logo_pulse = 1.0
        self.hologram_effect = 0.0

        self.enable_pulse = False
        self.enable_color_shift = False
        self.enable_hologram = False

        self.logo_hue_shift = 0.0
        self.beat_intensity = 0.0
        self.beat_decay = 0.90

        self.is_idle = True
        self.idle_phase = 0.0

        # --- state interpolasi sekarang berukuran HALF_BAR_COUNT ---
        # karena prev/next di-mirror saat dipakai, bukan disimpan dobel
        self.prev_bins = [0.0] * self.half_bar_count
        self.next_bins = [0.0] * self.half_bar_count
        self.last_arrival = time.perf_counter()
        self.interval_estimate = 0.08
        self._buffer_count = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)

        self.auto_load_logo()

    # ---------- logo ----------
    def auto_load_logo(self):
        for file in ("logo.png", "logo.jpg", "music_logo.png"):
            if os.path.exists(file):
                self.set_logo_image(file)
                return
        self.set_logo_text("osu!")

    def set_logo_image(self, image_path):
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            size = int(self.logo_radius * 1.3)
            self.logo_image = pixmap.scaled(
                size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.use_custom_logo = True
        else:
            self.use_custom_logo = False

    def set_logo_text(self, text, color=None):
        self.logo_text = text
        if color:
            self.fill_color = QColor(color)
        self.use_custom_logo = False

    # ---------- toggles ----------
    def toggle_pulse(self, enable=True):
        self.enable_pulse = enable

    def toggle_color_shift(self, enable=True):
        self.enable_color_shift = enable

    def toggle_hologram(self, enable=True):
        self.enable_hologram = enable

    # ---------- data masuk dari analyzer audio ----------
    # ---------- mirror helper ----------
    def _mirror_index(self, i):
        """Petakan indeks bar penuh (0..bar_count-1) ke indeks bin
        half-spectrum (0..half_bar_count-1), simetris kiri-kanan.
        i=0 -> paling atas (bass di kedua sisi), lalu turun ke kanan
        DAN ke kiri secara bersamaan menuju treble di paling bawah."""
        # jarak dari titik atas (index 0), searah jarum jam ATAU
        # berlawanan jarum jam, keduanya dihitung sebagai "half index" yang sama
        half = self.half_bar_count
        pos_in_half = i % half          # 0..half-1, bertambah searah jarum jam
        # sisi kanan (0..half-1) dan sisi kiri (half..bar_count-1) pakai
        # bin yang sama pada jarak sudut yang sama dari atas
        if i < half:
            return pos_in_half
        else:
            # sisi kiri: cerminkan urutannya supaya bass tetap di atas,
            # bukan di bawah (kalau tidak dibalik, treble akan ketemu duluan)
            return half - 1 - (i - half)

    def apply_fft_bins(self, bins):
        """bins: list of float 0..1, panjang == bar_count dari FFT asli.
        Kita cuma perlu HALF_BAR_COUNT nilai unik (bass->treble), sisanya
        adalah cermin. Ambil separuh pertama dari spektrum asli."""
        now = time.perf_counter()
        dt = now - self.last_arrival
        if 0.005 < dt < 1.0:
            self.interval_estimate = self.interval_estimate * 0.8 + dt * 0.2

        self.prev_bins = self.next_bins
        half = self.half_bar_count
        padded = list(bins[:half])
        if len(padded) < half:
            padded += [0.0] * (half - len(padded))
        self.next_bins = padded
        self.last_arrival = now
        self.is_idle = False

        self._buffer_count += 1
        if self._buffer_count % 30 == 0:
            print(f"[Visualizer] update audio ~{self.interval_estimate*1000:.0f}ms/frame")

        low = sum(bins[:10]) / max(1, len(bins[:10]))
        if low > 0.55:
            self.beat_intensity = min(1.0, self.beat_intensity + 0.35)

    def set_idle(self):
        self.is_idle = True

    # ---------- animasi tiap frame ----------
    def tick(self):
        self.beat_intensity *= self.beat_decay

        if self.is_idle:
            self.idle_phase += 0.05
            half = self.half_bar_count
            for i in range(self.bar_count):
                hi = self._mirror_index(i)
                wave = math.sin(self.idle_phase + hi * 0.25)
                self.target_heights[i] = self.bar_min + 10 * (0.5 + 0.5 * wave)
        else:
            now = time.perf_counter()
            interval = max(self.interval_estimate, 0.02)
            alpha = min(1.0, (now - self.last_arrival) / interval)
            span = self.bar_max - self.bar_min
            for i in range(self.bar_count):
                hi = self._mirror_index(i)
                v = self.prev_bins[hi] * (1 - alpha) + self.next_bins[hi] * alpha
                self.target_heights[i] = self.bar_min + max(0.0, min(1.0, v)) * span

        # --- attack instan, decay dengan rate berbeda (bukan smoothing simetris) ---
        # naik (attack) -> langsung snap ke target, biar responsif ke beat
        # turun (decay) -> pelan-pelan, biar tidak "kedip" patah-patah
        ATTACK = 1.0     # 1.0 = instan
        DECAY = 0.35      # makin kecil = makin cepat turun; sebelumnya efektif ~0.5 tapi ditumpuk 2x
        for i in range(self.bar_count):
            t = self.target_heights[i]
            cur = self.bar_heights[i]
            if t >= cur:
                self.bar_heights[i] = t  # attack instan, tidak diinterpolasi lagi
            else:
                self.bar_heights[i] = cur - (cur - t) * DECAY

        if self.enable_pulse:
            self.logo_pulse = 0.92 + 0.08 * math.sin(QTime.currentTime().msec() / 200)
            self.logo_pulse += 0.10 * self.beat_intensity
        else:
            self.logo_pulse = 1.0

        if self.enable_color_shift:
            speed = 0.3 + self.beat_intensity * 2.5
            self.logo_hue_shift = (self.logo_hue_shift + speed) % 360
            h = self.fill_color.hue() if self.fill_color.hue() >= 0 else 0
            s = self.fill_color.saturation()
            l = self.fill_color.lightness()
            new_h = int((h + self.logo_hue_shift) % 360)
            self.current_glow.setHsl(new_h, s, l)
        else:
            self.current_glow = QColor(self.fill_color)

        if self.enable_hologram:
            self.hologram_effect = (self.hologram_effect + 0.02) % (2 * math.pi)

        self.update()

    # ---------- paint ----------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        cx, cy = self.width() // 2, self.height() // 2

        painter.save()
        painter.translate(cx, cy)

        # --- 2 lingkaran tipis dekoratif paling luar (statis, khas osu!) ---
        deco_pen = QPen(QColor(255, 255, 255, 35), 1.5)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(deco_pen)
        painter.drawEllipse(QPoint(0, 0), self.deco_ring_1, self.deco_ring_1)
        deco_pen2 = QPen(QColor(255, 255, 255, 20), 1.2)
        painter.setPen(deco_pen2)
        painter.drawEllipse(QPoint(0, 0), self.deco_ring_2, self.deco_ring_2)

        # --- bar tipis rapat mengitari logo (spektrum audio) ---
        angle_step = 360 / self.bar_count
        for i, height in enumerate(self.bar_heights):
            angle = math.radians(i * angle_step - 90)  # mulai dari atas
            x1 = int(self.bar_start * math.cos(angle))
            y1 = int(self.bar_start * math.sin(angle))
            x2 = int((self.bar_start + height) * math.cos(angle))
            y2 = int((self.bar_start + height) * math.sin(angle))

            intensity = min(height / self.bar_max, 1.0)
            color = QColor(self.bar_color)
            color.setAlpha(int(120 + 135 * intensity))

            painter.setPen(QPen(color, 1.6))
            painter.drawLine(x1, y1, x2, y2)

        painter.restore()

        # --- logo besar: glow + cincin putih + isi pink ---
        painter.save()
        painter.translate(cx, cy)

        glow_radius = self.logo_radius + 15 + 10 * self.beat_intensity
        glow_color = QColor(self.bar_color)
        glow_color.setAlpha(50)
        painter.setBrush(glow_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPoint(0, 0), int(glow_radius), int(glow_radius))

        ring_radius = int(self.logo_radius * self.logo_pulse)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self.ring_color, self.ring_thickness))
        painter.drawEllipse(QPoint(0, 0), ring_radius, ring_radius)

        fill_radius = ring_radius - self.ring_thickness // 2
        painter.setBrush(self.current_glow)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPoint(0, 0), fill_radius, fill_radius)

        if self.enable_hologram:
            painter.save()
            painter.setClipRect(-fill_radius, -fill_radius, fill_radius * 2, fill_radius * 2)
            scan_y = int(fill_radius * math.sin(self.hologram_effect))
            painter.setPen(QPen(QColor(255, 255, 255, 30), 2))
            painter.drawLine(-fill_radius, scan_y, fill_radius, scan_y)
            painter.restore()

        if self.use_custom_logo and self.logo_image:
            img_size = int(self.logo_radius * 1.1 * self.logo_pulse)
            scaled_img = self.logo_image.scaled(
                img_size, img_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            rect = scaled_img.rect()
            rect.moveCenter(QPoint(0, 0))
            painter.drawPixmap(rect, scaled_img)
        else:
            font_size = int(self.logo_radius * 0.34 * self.logo_pulse)
            painter.setPen(QPen(QColor(255, 255, 255, 235), 1))
            painter.setFont(QFont("Segoe UI", font_size, QFont.Bold))
            text_box = int(self.logo_radius * 1.3)
            painter.drawText(
                QRect(-text_box // 2, -text_box // 2, text_box, text_box),
                Qt.AlignCenter, self.logo_text
            )

        painter.restore()


# ------------------------------------------------------------------
# AUDIO ANALYZER: sadap buffer PCM real dari QMediaPlayer -> FFT
# ------------------------------------------------------------------
class AudioAnalyzer(QWidget):
    """Bukan widget visual, cuma numpang QObject-lifecycle lewat QWidget
    supaya gampang di-parent. Tugasnya: probe audio -> FFT -> emit bins."""

    fft_ready = pyqtSignal(list)

    def __init__(self, player, bar_count=BAR_COUNT, parent=None):
        super().__init__(parent)
        self.bar_count = bar_count
        self.running_max = 1.0

        self.probe = QAudioProbe(self)
        self.probe.audioBufferProbed.connect(self.process_buffer)
        ok = self.probe.setSource(player)
        if not ok:
            print("[Audio Probe] setSource gagal - visualizer akan pakai mode idle.")

    def process_buffer(self, buffer):
        try:
            fmt = buffer.format()
            if fmt.sampleType() != QAudioFormat.SignedInt or fmt.sampleSize() != 16:
                # format selain PCM16 signed belum di-handle -> skip frame ini
                return

            sample_rate = fmt.sampleRate()
            channels = fmt.channelCount()

            data_ptr = buffer.constData()
            data_ptr.setsize(buffer.byteCount())
            raw = bytes(data_ptr)

            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            if len(samples) == 0:
                return

            if channels > 1:
                usable = len(samples) - (len(samples) % channels)
                samples = samples[:usable].reshape(-1, channels).mean(axis=1)

            if len(samples) < 64:
                return

            window = np.hanning(len(samples))
            windowed = samples * window
            spectrum = np.fft.rfft(windowed)
            mag = np.abs(spectrum)
            freqs = np.fft.rfftfreq(len(windowed), d=1.0 / sample_rate)

            bins = self._bucket_log(mag, freqs, sample_rate)
            self.fft_ready.emit(bins)
        except Exception as e:
            # jangan biarkan error audio-processing bikin app crash
            print(f"[Audio Probe] error saat proses buffer: {e}")

    def _bucket_log(self, mag, freqs, sample_rate):
        nyquist = max(1000, sample_rate / 2 - 1)
        edges = np.logspace(np.log10(20), np.log10(min(16000, nyquist)),
                             num=self.bar_count + 1)
        bins = np.zeros(self.bar_count, dtype=np.float32)
        for i in range(self.bar_count):
            lo, hi = edges[i], edges[i + 1]
            mask = (freqs >= lo) & (freqs < hi)
            if np.any(mask):
                bins[i] = mag[mask].mean()

        peak = bins.max()
        # decay lebih cepat (0.90) supaya auto-gain lebih responsif mengikuti
        # naik-turunnya volume musik, bukan lambat ngikutin puncak lama
        self.running_max = max(self.running_max * 0.90, float(peak) if peak > 0 else 1.0)
        norm = np.clip(bins / self.running_max, 0.0, 1.0)
        # boost dinamika: pangkat <1 supaya bagian pelan lebih kelihatan gerak
        norm = np.power(norm, 0.55)
        return norm.tolist()


# ------------------------------------------------------------------
# CONTROL PANEL (pojok kanan atas)
# ------------------------------------------------------------------
class ControlPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(300, 150)

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.setStyleSheet("""
            QWidget#panel {
                background-color: rgba(15, 15, 20, 160);
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 25);
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 15);
                color: white;
                border: none;
                border-radius: 18px;
                font-size: 15px;
                min-width: 34px;
                min-height: 34px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 30); }
            QPushButton:pressed { background-color: rgba(29, 185, 84, 60); }
            QLabel { color: white; font-size: 11px; }
            QLabel#time { color: #aaa; font-size: 10px; }
            QSlider::groove:horizontal {
                height: 3px; background: rgba(255,255,255,30); border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #1DB954; width: 10px; height: 10px;
                margin: -3.5px 0; border-radius: 5px;
            }
            QSlider::sub-page:horizontal { background: #1DB954; border-radius: 2px; }
        """)

        self.drag_pos = None
        self.pin_top = True

        self._build_ui()
        self._init_player()

    # ---------- UI ----------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QWidget(objectName="panel")
        self._panel = panel
        outer.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        controls.addStretch()

        self.btn_prev = QPushButton("⏮")
        self.btn_prev.setFixedSize(34, 34)
        self.btn_prev.clicked.connect(self.prev_track)
        controls.addWidget(self.btn_prev)

        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedSize(42, 42)
        self.btn_play.setStyleSheet(
            "font-size: 16px; background-color: rgba(29,185,84,60);"
        )
        self.btn_play.clicked.connect(self.toggle_play)
        controls.addWidget(self.btn_play)

        self.btn_next = QPushButton("⏭")
        self.btn_next.setFixedSize(34, 34)
        self.btn_next.clicked.connect(self.next_track)
        controls.addWidget(self.btn_next)

        controls.addStretch()
        layout.addLayout(controls)

        self.label_track = QLabel("[No Music]")
        self.label_track.setStyleSheet("color: #ddd; font-size: 11px;")
        layout.addWidget(self.label_track)

        self.slider_progress = QSlider(Qt.Horizontal)
        self.slider_progress.setRange(0, 0)
        self.slider_progress.sliderMoved.connect(self.seek_position)
        layout.addWidget(self.slider_progress)

        time_row = QHBoxLayout()
        self.label_elapsed = QLabel("00:00", objectName="time")
        self.label_duration = QLabel("00:00", objectName="time")
        time_row.addWidget(self.label_elapsed)
        time_row.addStretch()
        time_row.addWidget(self.label_duration)
        layout.addLayout(time_row)

        vol_row = QHBoxLayout()
        vol_label = QLabel("🔊")
        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(70)
        self.slider_volume.valueChanged.connect(self.set_volume)
        vol_row.addWidget(vol_label)
        vol_row.addWidget(self.slider_volume)
        layout.addLayout(vol_row)

    # ---------- player ----------
    def _init_player(self):
        self.player = QMediaPlayer()
        self.player.setVolume(70)
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.stateChanged.connect(self.update_play_button)
        self.player.mediaStatusChanged.connect(self.media_status_changed)
        self.player.error.connect(self.handle_error)

        self.track_list = []
        self.current_index = -1
        self._fail_count = 0
        self.load_tracks()

    def load_tracks(self):
        folder = Path(MUSIC_FOLDER)
        if not folder.exists():
            self.label_track.setText("Folder tidak ditemukan")
            print(f"[Player] Folder not found: {MUSIC_FOLDER}")
            return

        found = set()
        for ext in SUPPORTED_EXT:
            found.update(folder.glob(f"*{ext}"))
            found.update(folder.glob(f"**/*{ext}"))

        self.track_list = list(found)

        if self.track_list:
            random.shuffle(self.track_list)
            self.current_index = 0
            self.label_track.setText(self.track_list[0].stem)
            print(f"[Player] Found {len(self.track_list)} songs in {MUSIC_FOLDER}")
        else:
            self.label_track.setText("[Tidak ada file musik]")
            print(f"[Player] No music files found. Supported: {SUPPORTED_EXT}")

    def start_playback(self):
        """Dipanggil dari MainApp SETELAH AudioAnalyzer terpasang, supaya
        probe sudah aktif sebelum lagu pertama mulai."""
        self.play_current()

    def play_current(self):
        if not (0 <= self.current_index < len(self.track_list)):
            return
        track_path = str(self.track_list[self.current_index])
        print(f"[Player] Loading: {track_path}")
        self.player.setMedia(QMediaContent(QUrl.fromLocalFile(track_path)))
        self.player.play()
        name = self.track_list[self.current_index].stem
        if len(name) > 32:
            name = name[:29] + "..."
        self.label_track.setText(f"{name}")

    def toggle_play(self):
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
        elif self.player.mediaStatus() in (QMediaPlayer.NoMedia, QMediaPlayer.InvalidMedia):
            # belum ada media ter-load dengan benar -> load ulang track saat ini
            self.play_current()
        else:
            self.player.play()

    def next_track(self):
        if not self.track_list:
            return
        self.current_index = (self.current_index + 1) % len(self.track_list)
        self.play_current()

    def prev_track(self):
        if not self.track_list:
            return
        self.current_index = (self.current_index - 1) % len(self.track_list)
        self.play_current()

    def set_volume(self, val):
        self.player.setVolume(val)

    def update_position(self, pos):
        self.slider_progress.setValue(pos)
        self.label_elapsed.setText(self._fmt_ms(pos))

    def update_duration(self, dur):
        self.slider_progress.setRange(0, dur)
        self.label_duration.setText(self._fmt_ms(dur))

    def update_play_button(self, state):
        self.btn_play.setText("⏸" if state == QMediaPlayer.PlayingState else "▶")

    _STATUS_NAMES = {
        0: "UnknownMediaStatus", 1: "NoMedia", 2: "LoadingMedia",
        3: "LoadedMedia", 4: "StalledMedia", 5: "BufferingMedia",
        6: "BufferedMedia", 7: "EndOfMedia", 8: "InvalidMedia",
    }

    def media_status_changed(self, status):
        print(f"[Player] mediaStatus -> {self._STATUS_NAMES.get(status, status)}")
        if status == QMediaPlayer.InvalidMedia:
            print("[Player] File tidak bisa dibaca (codec/format tidak didukung "
                  "backend multimedia Windows kamu). Coba file .mp3 lain / install "
                  "K-Lite Codec Pack, atau convert filenya.")
        if status == QMediaPlayer.EndOfMedia:
            self._fail_count = 0
            self.next_track()

    def handle_error(self, error):
        if error == QMediaPlayer.NoError:
            return
        err_str = self.player.errorString()
        print(f"[Player] Error playing track: {err_str} (code={error})")
        if "0x80040266" in err_str or "VFW_E_CANNOT_RENDER" in err_str or "doRender" in err_str:
            print(
                "[Player] >> DirectShow tidak nemu codec buat file ini.\n"
                "   Coba salah satu:\n"
                "   1. Sudah otomatis dicoba paksa backend WMF (restart script).\n"
                "      Kalau masih gagal setelah restart, backend WMF juga\n"
                "      tidak dukung file ini.\n"
                "   2. Install LAV Filters (https://github.com/Nevcairiel/LAVFilters/releases)\n"
                "      biar DirectShow dukung lebih banyak codec.\n"
                "   3. Convert file itu ke .mp3 standar (misal pakai ffmpeg),\n"
                "      lalu coba lagi.\n"
                "   4. Coba dulu dengan file MP3 lain yang kamu tahu pasti normal,\n"
                "      buat mastiin ini soal 1 file itu atau soal setup Windows-nya."
            )
        self._fail_count += 1
        if self._fail_count < len(self.track_list):
            self.next_track()  # skip file yang bermasalah
        else:
            print("[Player] Semua track gagal diputar, cek codec/format file.")

    def seek_position(self, pos):
        self.player.setPosition(pos)

    @staticmethod
    def _fmt_ms(ms):
        s = int(ms / 1000)
        return f"{s // 60:02d}:{s % 60:02d}"

    # ---------- drag & shortcuts ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPos()

    def mouseMoveEvent(self, event):
        if self.drag_pos is not None:
            delta = event.globalPos() - self.drag_pos
            self.move(self.pos() + delta)
            self.drag_pos = event.globalPos()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.toggle_play()
        elif event.key() == Qt.Key_Right:
            self.next_track()
        elif event.key() == Qt.Key_Left:
            self.prev_track()
        elif event.key() == Qt.Key_F9:
            self.toggle_pin()
        elif event.key() == Qt.Key_Escape:
            QApplication.quit()

    def toggle_pin(self):
        self.pin_top = not self.pin_top
        flags = Qt.FramelessWindowHint | Qt.Tool
        flags |= Qt.WindowStaysOnTopHint if self.pin_top else Qt.WindowStaysOnBottomHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()


# ------------------------------------------------------------------
# MAIN APP
# ------------------------------------------------------------------
class MainApp(QWidget):
    def __init__(self):
        super().__init__()

        self.visualizer = VisualizerWidget()
        self.control_panel = ControlPanel()  # ini cuma load_tracks(), belum play

        self.analyzer = AudioAnalyzer(self.control_panel.player, BAR_COUNT, parent=self)
        self.analyzer.fft_ready.connect(self._on_fft)

        self._last_fft_time = QTime.currentTime()
        self.idle_watchdog = QTimer(self)
        self.idle_watchdog.timeout.connect(self._check_idle)
        self.idle_watchdog.start(400)

        screen = QApplication.primaryScreen().geometry()
        self.control_panel.move(screen.width() - 320, 30)
        vw, vh = self.visualizer.width(), self.visualizer.height()
        self.visualizer.move(screen.width() // 2 - vw // 2, screen.height() // 2 - vh // 2)

        self.show()

        # baru mulai play SETELAH probe audio terpasang & widget tampil
        QTimer.singleShot(150, self.control_panel.start_playback)

    def _on_fft(self, bins):
        self._last_fft_time = QTime.currentTime()
        self.visualizer.apply_fft_bins(bins)

    def _check_idle(self):
        is_playing = self.control_panel.player.state() == QMediaPlayer.PlayingState
        elapsed = self._last_fft_time.msecsTo(QTime.currentTime())
        if not is_playing or elapsed > 500:
            self.visualizer.set_idle()

    def show(self):
        self.visualizer.show()
        self.control_panel.show()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    main = MainApp()

    quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), main.control_panel)
    quit_shortcut.activated.connect(app.quit)

    sys.exit(app.exec_())