# CPU Scheduler Benchmark

FCFS (First-Come, First-Served) ve Round Robin CPU zamanlama algoritmalarını aynı iş yükü üzerinde simüle eden, karşılaştıran ve deney çıktıları üreten Tkinter tabanlı masaüstü uygulaması.

Bu repo, düzeltilmiş **Benchmark v5 — BatchFix** sürümünü içerir. Sürümde simülasyon sonuçlarının döngü tamamlandıktan sonra hesaplanması, boş iş yükü güvenliği, batch çıktı klasörünün otomatik oluşturulması ve ayrıntılı hata raporlama iyileştirmeleri bulunur.

## Özellikler

- FCFS ve Round Robin zamanlama simülasyonu
- Ayarlanabilir Round Robin quantum değeri
- Ayarlanabilir context-switch overhead değeri
- Rastgele veya elle süreç ekleme
- Arrival time ve burst time desteği
- Tick tabanlı çalışma, 10 tick ilerletme ve sonuna kadar çalıştırma
- Ready, pending ve tamamlanan süreçlerin canlı görüntülenmesi
- Aynı iş yükünde FCFS–RR karşılaştırması
- Çoklu seed ile batch deneyleri
- Üç hazır deney senaryosu
- Quantum sweep analizi
- Eşleştirilmiş hipotez testi
- CSV raporları ve PNG grafikler
- Jain fairness index hesaplaması
- Ayrıntılı traceback içeren batch hata bildirimi

## Hesaplanan metrikler

- Ortalama bekleme süresi
- Ortalama response time
- Ortalama turnaround time
- CPU utilization
- Throughput
- Context-switch sayısı ve toplam context-switch süresi
- Idle time
- Bekleme ve turnaround süreleri için Jain fairness index
- Süreç bazında dispatch ve preemption sayıları

## Gereksinimler

- Python 3.10 veya üzeri
- Tkinter
- Matplotlib
- SciPy

Tkinter çoğu Windows Python kurulumuyla birlikte gelir. Bazı Linux dağıtımlarında ayrıca kurulması gerekebilir:

```bash
sudo apt install python3-tk
```

## Kurulum

Repoyu klonlayın:

```bash
git clone https://github.com/yaprakagirman/cpu-scheduler-benchmark.git
cd cpu-scheduler-benchmark
```

İsteğe bağlı olarak sanal ortam oluşturun:

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Linux veya macOS:

```bash
source .venv/bin/activate
```

Bağımlılıkları kurun:

```bash
python -m pip install -r requirements.txt
```

## Çalıştırma

```bash
python scheduler_benchmark.py
```

Uygulama açıldıktan sonra:

1. FCFS veya Round Robin algoritmasını seçin.
2. Round Robin için quantum değerini belirleyin.
3. Gerekirse context-switch overhead değerini ayarlayın.
4. Rastgele ya da elle süreç ekleyin.
5. Simülasyonu tick bazında veya sonuna kadar çalıştırın.
6. **Metrikler** ya da **RR vs FCFS** düğmesiyle sonuçları inceleyin.

## Batch deneyleri

Arayüzdeki **Batch Deney** düğmesi aşağıdaki parametreleri kabul eder:

- Senaryo: `ALL`, `S1_light`, `S2_heavy` veya `S3_bursty`
- Seed sayısı
- Hipotez testinde kullanılacak RR quantum değeri
- Quantum sweep listesi
- Context-switch overhead
- Çıktı klasörü

### Deney senaryoları

| Senaryo | Açıklama |
| --- | --- |
| `S1_light` | Daha hafif ve dengeli iş yükü |
| `S2_heavy` | Daha uzun CPU burst değerlerine sahip yoğun iş yükü |
| `S3_bursty` | Kısa ve uzun işlerin birlikte bulunduğu değişken iş yükü |
| `ALL` | Üç senaryonun tamamını çalıştırır |

### Üretilen dosyalar

| Dosya | İçerik |
| --- | --- |
| `results_raw.csv` | Her seed ve algoritma için ham deney sonuçları |
| `results_summary.csv` | Senaryo ve algoritma bazında ortalama ve SEM değerleri |
| `per_process.csv` | Süreç bazında bekleme, response ve turnaround değerleri |
| `quantum_sweep_raw.csv` | Farklı quantum değerlerinin ham sonuçları |
| `hypothesis_tests.csv` | FCFS–RR eşleştirilmiş hipotez testi sonuçları |
| `report.txt` | Deney ayarları ve oluşturulan çıktıların kısa özeti |
| `bar_*.png` | Temel metriklerin ortalama ± SEM grafikleri |
| `line_*.png` | Quantum sweep çizgi grafikleri |
| `box_*_waiting.png` | Bekleme süresi dağılım grafikleri |

SciPy bulunamazsa uygulama normal yaklaşım tabanlı bir test sonucu üretir. Matplotlib bulunamazsa CSV ve metin çıktıları yine oluşturulur, grafik üretme hatası `report.txt` içine kaydedilir.

## Proje yapısı

```text
cpu-scheduler-benchmark/
├── scheduler_benchmark.py   # Simülasyon, arayüz ve batch deney kodu
├── tests/
│   └── test_simulation.py   # Temel simülasyon doğrulama testleri
├── requirements.txt         # Grafik ve istatistik bağımlılıkları
├── .gitignore
└── README.md
```

## Testler

Testler Python standart kütüphanesindeki `unittest` ile yazılmıştır:

```bash
python -m unittest discover -s tests -v
```

Testler boş iş yükünü, deterministik FCFS sonucunu ve Round Robin tamamlanma davranışını kontrol eder.

## Algoritma notları

- FCFS non-preemptive çalışır; başlayan süreç tamamlanana kadar CPU üzerinde kalır.
- Round Robin, quantum süresi dolan tamamlanmamış süreci ready kuyruğunun sonuna gönderir.
- Context-switch overhead sırasında CPU gerçek bir süreç çalıştırmaz; bu süre utilization hesabında busy time olarak sayılmaz.
- Her iki algoritma aynı iş yüküyle çalıştırılarak karşılaştırmanın adil olması sağlanır.
- Simülasyon motoru kullanıcı arayüzünden bağımsız `simulate()` fonksiyonu üzerinden test edilebilir.

## Sürüm

**Benchmark v5 — BatchFix**

- Erken sonuç döndürmeye neden olan simülasyon döngüsü girinti problemi düzeltildi.
- Boş iş yükü için güvenli sonuç eklendi.
- Batch çıktı klasörü mutlak yola çevrilerek otomatik oluşturuluyor.
- Batch hatalarında ayrıntılı traceback gösteriliyor.

