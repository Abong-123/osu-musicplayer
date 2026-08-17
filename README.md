# 🎵 osu-musicplayer

<p align="center"> <img src="https://img.shields.io/badge/Python-3.7+-blue?style=flat-square" alt="Python" height="25"> &nbsp;&nbsp; <img src="https://img.shields.io/badge/PyQt5-5.15.9-green?style=flat-square" alt="PyQt5" height="25"> &nbsp;&nbsp; <img src="https://img.shields.io/badge/NumPy-1.21+-orange?style=flat-square" alt="NumPy" height="25"> </p><p align="center"> </p>

---

## 📸 Screenshot

![Screenshot](img/screenshoot.png)

---

## 📝 Deskripsi

Aplikasi desktop widget pemutar musik dengan visualizer melingkar terinspirasi dari osu!. Menampilkan spektrum audio dalam bentuk bar melingkar mengelilingi logo, dilengkapi dengan kontrol pemutar musik sederhana.

**Fitur:**
- Visualizer spektrum audio melingkar
- Kontrol pemutar (play/pause, next, prev, volume, progress)
- Drag panel kontrol untuk memposisikan
- Shortcut keyboard (Space, F9, Esc, dll)
- Always on top toggle (F9)

---

## 🚀 Cara Menjalankan

### 1. Clone Repository

```bash
git clone https://github.com/username/osu-musicplayer.git
cd osu-musicplayer
```

### 2. Buat Virtual Environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install PyQt5==5.15.9 numpy
```

### 4. Sesuaikan Folder Musik

Buka file `osu_music_widget.py` dan ubah path pada baris:

```python
MUSIC_FOLDER = r"C:\Users\[users]\Music"  # Ganti dengan folder musik kamu
```

**Contoh:**
```python
MUSIC_FOLDER = r"D:\Musik"
# atau
MUSIC_FOLDER = r"C:\Users\JohnDoe\Music"
```

### 5. Jalankan Aplikasi

```bash
python osu_music_widget.py
```

---

## ⌨️ Shortcut Keyboard

| Tombol | Fungsi |
|--------|--------|
| `Space` | Play/Pause |
| `→` | Lagu berikutnya |
| `←` | Lagu sebelumnya |
| `F9` | Toggle Always on Top |
| `Esc` / `Ctrl+Q` | Keluar aplikasi |

---

## 📦 Dependencies

- Python 3.7+
- PyQt5 5.15.9
- NumPy 1.21+

---

## 📄 License

MIT License - lihat file [LICENSE](LICENSE) untuk detail.