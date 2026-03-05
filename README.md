# 🎬 Subtitle Tool

> **Tự động thêm phụ đề tiếng Anh + watermark vào video YouTube — chạy offline, miễn phí.**

Hỗ trợ cả **CLI** (command line) và **Desktop GUI** (giao diện đồ họa).

---

## ✨ Tính năng

| Tính năng                | Mô tả                                                                                         |
| ------------------------ | --------------------------------------------------------------------------------------------- |
| 📥 **Tải video YouTube** | Tự chọn chất lượng theo thời lượng (1080p / 720p)                                             |
| 🗣️ **Whisper AI**        | Tạo phụ đề tiếng Anh offline bằng [faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| 🏷️ **Watermark**         | Text "Daisy" — white text, background 60% opacity                                             |
| 🔤 **Subtitle style**    | Font Arial, background 60% opacity (giống watermark)                                          |
| 🧹 **Auto-clean**        | Loại bỏ `[music]`, `[applause]`... khỏi subtitle                                              |
| ✂️ **Smart split**       | Mỗi dòng subtitle ≤ 45 ký tự (không bị xuống dòng)                                            |
| 📊 **Quality Check**     | Phân tích chất lượng subtitle (CPS, timing, format, scoring A-F)                              |
| 📦 **Batch processing**  | Xử lý hàng loạt từ kênh YouTube                                                               |
| 🖥️ **Desktop GUI**       | Dark mode, progress tracking, quality score                                                   |
| 📊 **Progress bar**      | Realtime khi encode FFmpeg (CLI + GUI)                                                        |

---

## 📋 Yêu cầu hệ thống

| Yêu cầu              | Phiên bản                        |
| -------------------- | -------------------------------- |
| **Python**           | ≥ 3.10                           |
| **FFmpeg**           | Bất kỳ (cần cài sẵn)             |
| **GPU** _(tùy chọn)_ | NVIDIA + CUDA (tăng tốc Whisper) |

### Cài FFmpeg

```bash
# Windows (Chocolatey)
choco install ffmpeg

# Windows (Scoop)
scoop install ffmpeg

# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

> 💡 Kiểm tra: `ffmpeg -version`

---

## 🚀 Cài đặt

```bash
# 1. Clone project
git clone https://github.com/YOUR_USERNAME/toolAddSubtitle.git
cd toolAddSubtitle

# 2. Tạo virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# 3. Cài dependencies
pip install -e .          # CLI only
pip install -e ".[gui]"   # CLI + GUI

# 4. Tạo file cấu hình (tùy chọn)
cp .env.example .env      # macOS/Linux
copy .env.example .env    # Windows
```

---

## 🎮 Sử dụng

### 🖥️ Desktop GUI _(khuyến nghị)_

```bash
python -m subtitle_tool --gui
```

hoặc:

```bash
subtitle-tool-gui
```

**Tính năng GUI:**

- Dán link → **Add** → **Start All**
- Hỗ trợ batch processing (nhiều video cùng queue)
- Nút **Stop** để dừng sau video hiện tại
- Progress bar + log realtime
- **Quality score** hiển thị khi hoàn thành (ví dụ: `✅ Done · 45 MB · Q:85(B)`)
- Tự nhớ đường dẫn FFmpeg

### ⌨️ CLI — Xử lý 1 video

```bash
python -m subtitle_tool --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

### ⌨️ CLI — Xử lý nhiều video từ kênh

```bash
python -m subtitle_tool --channel "https://www.youtube.com/@ChannelName/videos" --max 5
```

### ⌨️ CLI — Tùy chỉnh nâng cao

```bash
python -m subtitle_tool \
  --url "https://youtube.com/watch?v=xxx" \
  --watermark "Daisy" \
  --model medium \
  --device auto \
  --ffmpeg "C:\ffmpeg\bin\ffmpeg.exe" \
  --output ./output \
  --verbose
```

### 📊 CLI — Kiểm tra chất lượng subtitle

```bash
python -m subtitle_tool --check-quality path/to/file.srt
```

Phân tích file `.srt` mà không cần download/encode video:

- **Score 0–100** với grade A–F
- Phân tích timing (CPS, overlap, duration)
- Phân tích format (line length, empty entries, artifacts)
- Exit code: `0` nếu score ≥ 60, `1` nếu < 60

---

## ⚙️ CLI Options

| Option               | Mô tả                                                             | Mặc định   |
| -------------------- | ----------------------------------------------------------------- | ---------- |
| `--url`              | URL video YouTube                                                 | —          |
| `--channel`          | URL kênh YouTube                                                  | —          |
| `--check-quality`    | Phân tích chất lượng file SRT                                     | —          |
| `--gui`              | Mở Desktop GUI                                                    | off        |
| `--output` / `-o`    | Thư mục output                                                    | `./output` |
| `--max`              | Số video tối đa (batch)                                           | `10`       |
| `--watermark` / `-w` | Text watermark                                                    | `Daisy`    |
| `--model`            | Whisper model (`tiny` / `base` / `small` / `medium` / `large-v3`) | `medium`   |
| `--device`           | Compute device (`auto` / `cpu` / `cuda`)                          | `auto`     |
| `--ffmpeg`           | Đường dẫn FFmpeg binary                                           | `ffmpeg`   |
| `--verbose` / `-v`   | Log chi tiết                                                      | off        |

---

## 🔧 Cấu hình môi trường (`.env`)

```ini
# Whisper model: tiny, base, small, medium, large-v3
WHISPER_MODEL=medium

# Compute device: auto, cpu, cuda
WHISPER_DEVICE=auto

# Output and temp directories
SUBTITLE_OUTPUT_DIR=./output
SUBTITLE_TEMP_DIR=./temp

# FFmpeg binary path (if not in PATH)
FFMPEG_PATH=ffmpeg
```

---

## 📊 Quality Scoring

Hệ thống chấm điểm chất lượng subtitle **(0–100 điểm)**:

### Trọng số

| Hạng mục     | Trọng số | Chi tiết                              |
| ------------ | -------- | ------------------------------------- |
| **Timing**   | 40 điểm  | CPS, duration, overlaps               |
| **Format**   | 30 điểm  | Line length, empty entries, artifacts |
| **Coverage** | 30 điểm  | Entry count, gaps, average CPS        |

### Bảng xếp hạng

| Grade | Score | Ý nghĩa        |
| ----- | ----- | -------------- |
| 🌟 A  | ≥ 90  | Xuất sắc       |
| ✅ B  | 80–89 | Tốt            |
| ⚠️ C  | 70–79 | Chấp nhận được |
| 🟡 D  | 60–69 | Cần cải thiện  |
| ❌ F  | < 60  | Không đạt      |

### Ngưỡng kiểm tra

| Metric               | Giá trị                 |
| -------------------- | ----------------------- |
| CPS (lý tưởng)       | 10–25 chars/sec         |
| CPS (cảnh báo)       | < 5 hoặc > 35 chars/sec |
| Duration (per entry) | 0.5s–8.0s               |
| Max line length      | 45 ký tự                |
| Gap warning          | > 5.0s giữa 2 entry     |

---

## 🏗️ Cấu trúc dự án

```
toolAddSubtitle/
├── pyproject.toml                # Cấu hình project & dependencies
├── .env.example                  # Mẫu biến môi trường
├── .gitignore                    # Bộ lọc file cho Git
├── README.md                     # Tài liệu (file này)
├── template.jpg                  # Template ảnh
│
├── src/subtitle_tool/            # Source code chính
│   ├── __init__.py               # Package init + version
│   ├── __main__.py               # Entry point: python -m subtitle_tool
│   ├── cli.py                    # CLI argument parsing
│   ├── gui.py                    # Desktop GUI (customtkinter)
│   ├── config.py                 # Cấu hình (Watermark, Caption, Whisper)
│   ├── downloader.py             # Tải video YouTube (yt-dlp)
│   ├── transcriber.py            # Tạo phụ đề (faster-whisper)
│   ├── srt_utils.py              # Xử lý SRT: normalize, split, filter
│   ├── processor.py              # Burn subtitle + watermark (FFmpeg)
│   ├── pipeline.py               # Orchestrator: download → transcribe → burn
│   ├── quality.py                # Phân tích chất lượng subtitle
│   └── exceptions.py             # Custom exceptions
│
├── tests/                        # Unit tests
│   └── __init__.py
│
├── output/                       # Video đã xử lý (git ignored)
└── temp/                         # File tạm (git ignored)
```

---

## 🔄 Pipeline xử lý

```
YouTube URL
  │
  ▼
1. Validate URL (youtube.com / youtu.be)
  │
  ▼
2. Download video + auto-subtitle (yt-dlp)
  │  ├─ < 10 phút → 1080p
  │  ├─ 10-26 phút → 720p
  │  └─ > 26 phút → Bỏ qua
  │
  ▼
3. Transcribe
  │  ├─ Có YouTube auto-sub → sử dụng luôn
  │  └─ Không có → Whisper AI (offline)
  │
  ▼
4. Normalize SRT
  │  ├─ Ghép text multi-line → 1 dòng
  │  ├─ Loại bỏ [music], [applause]...
  │  ├─ Cắt text > 45 ký tự → nhiều entry ngắn
  │  └─ Xóa timestamp overlap
  │
  ▼
5. Quality Check
  │  ├─ Phân tích CPS (tốc độ đọc)
  │  ├─ Kiểm tra timing & format
  │  └─ Tính điểm 0-100, grade A-F
  │
  ▼
6. Generate FFmpeg filter script
  │  ├─ Watermark "Daisy" (góc trên phải)
  │  └─ Subtitle (giữa dưới, cùng style)
  │
  ▼
7. Encode video (H.264, CRF 23, AAC 128k)
  │  └─ Progress bar realtime
  │
  ▼
8. Cleanup temp files
  │
  ▼
Output: video_subtitled.mp4 + Quality Score
```

---

## 🔀 Chuyển sang máy khác

```bash
# 1. Clone repo
git clone https://github.com/YOUR_USERNAME/toolAddSubtitle.git
cd toolAddSubtitle

# 2. Cài đặt
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -e ".[gui]"

# 3. Cấu hình
copy .env.example .env          # Windows
# cp .env.example .env          # macOS/Linux

# 4. Chạy
python -m subtitle_tool --gui
```

> ⚠️ Nhớ cài **FFmpeg** trên máy mới: `choco install ffmpeg`

---

## 📝 Changelog

### v1.2.0

- 📊 **Quality Check** — phân tích chất lượng subtitle tự động
  - Scoring 0–100 với grade A–F (Timing 40pt + Format 30pt + Coverage 30pt)
  - Phân tích CPS, timing, format
  - Phát hiện overlap, empty entries, artifacts
- 🔍 **Standalone mode** — `--check-quality file.srt`
- 🖥️ **GUI quality display** — hiển thị quality score khi video hoàn thành

### v1.1.0

- 🖥️ Desktop GUI (batch processing, dark mode)
- 📊 Progress bar realtime khi encode FFmpeg
- 🛡️ Validate YouTube URL trước khi download
- 🔒 Escape watermark text cho FFmpeg safety
- 🧹 Tự dọn temp files sau khi encode

### v1.0.0

- 🎉 Release đầu tiên: CLI, download, transcribe, burn subtitles + watermark

---

## 🤝 Contributing

1. Fork repo
2. Tạo branch: `git checkout -b feature/ten-tinh-nang`
3. Commit: `git commit -m "feat: mô tả thay đổi"`
4. Push: `git push origin feature/ten-tinh-nang`
5. Tạo Pull Request

---

## 📄 License

MIT License — Sử dụng tự do cho mục đích cá nhân và thương mại.

---

**Made with ❤️ by VinhDat**
