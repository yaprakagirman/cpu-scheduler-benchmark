import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Tuple
import random

import os
import csv
import math
import statistics
import traceback

# ----------------------------
# Makale / deney çıktıları için yardımcı fonksiyonlar
# ----------------------------
def jains_fairness(values: List[float]) -> float:
    """Jain's Fairness Index. 1'e yaklaştıkça daha adil."""
    vals = [float(v) for v in values]
    if not vals:
        return 1.0
    s = sum(vals)
    sq = sum(v * v for v in vals)
    if sq == 0:
        return 1.0
    return (s * s) / (len(vals) * sq)

def mean_sem(values: List[float]) -> Tuple[float, float]:
    """(mean, SEM) = (ortalama, standart hata)."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return (0.0, 0.0)
    if len(vals) == 1:
        return (vals[0], 0.0)
    mu = statistics.mean(vals)
    sd = statistics.stdev(vals)
    sem = sd / math.sqrt(len(vals))
    return (mu, sem)

def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)




# ----------------------------
# PROCESS MODELİ
# ----------------------------
@dataclass
class Process:
    """
    name     : P1, P2 ...
    burst    : CPU'da toplam çalışması gereken süre (service time)
    arrival  : sisteme/ready'e gelme zamanı (mutlak zaman tick)
    remaining: kalan burst (sim sırasında azalır)

    start    : CPU'ya ilk kez alındığı tick
    finish   : tamamen bittiği tick

    waiting  : ready kuyruğunda toplam beklediği süre
    response : CPU'ya ilk kez alınana kadar geçen süre (start - arrival)

    last_ready:
        prosesin en son READY kuyruğuna girdiği an.
        WAIT hesaplamak için kritik:
            WAIT += (dispatch_time - last_ready)
        RR'de preemption olunca last_ready = o an (tekrar kuyruğa girdiği an) yapılır.
    """
    name: str
    burst: int
    arrival: int
    remaining: int = field(init=False)

    start: Optional[int] = None
    finish: Optional[int] = None

    waiting: int = 0
    response: Optional[int] = None

    last_ready: int = field(init=False)

    # RR için:
    quantum_left: int = 0

    # Debug / analiz için sayaçlar:
    dispatches: int = 0
    preemptions: int = 0

    def __post_init__(self) -> None:
        self.remaining = self.burst
        self.last_ready = self.arrival


# ============================================================
#  UI'DEN BAĞIMSIZ "ADİL" KARŞILAŞTIRMA SİMÜLASYONU
#  compare() fonksiyonunda kullanılıyor:
#  - Aynı workload'u RR ve FCFS'e veriyoruz
#  - UI akışından bağımsız, otomatik tamamlanana kadar simülasyon
# ============================================================
def simulate(workload: List[Tuple[str, int, int]], algo: str, quantum: int, overhead: int) -> Dict:
    """
    workload: [(name, burst, arrival), ...]
    algo    : "RR" veya "FCFS"
    quantum : RR için time slice
    overhead: context switch overhead tick sayısı (0..3 gibi)

    Sim akışı:
    - pending: daha gelmemiş prosesler (arrival > t)
    - ready  : CPU bekleyen prosesler (arrival <= t)
    - running: CPU üzerinde çalışan tek proses
    - overhead_left > 0 ise CPU "CS" (context switch) ile meşgul kabul edilir.

    Context switch sayımı:
    - switch_pending True -> bir önceki proses bitti ya da preempt oldu
    - bir sonraki DISPATCH anında ctx_switches += 1 yapılır
      (Yani "switch gerçekleşti"yi bir sonraki çalıştırma anında sayıyoruz.)

    Not:
    - Bu fonksiyon UI'den bağımsızdır; Compare ve Batch deneyde kullanılır.
    """

    # Boş workload güvenliği
    if not workload:
        return {
            "total_time": 0,
            "busy_time": 0,
            "cpu_util": 0.0,
            "ctx_switches": 0,
            "algo": algo,
            "quantum": quantum if algo == "RR" else None,
            "overhead": overhead,
            "cs_time": 0,
            "idle_time": 0,
            "throughput": 0.0,
            "avg_tat": 0.0,
            "fairness_wait": 1.0,
            "fairness_tat": 1.0,
            "done": [],
            "per_process": [],
            "gantt": [],
            "avg_wait": 0.0,
            "avg_resp": 0.0,
        }

    # Process objelerini üret
    procs = [Process(name=n, burst=b, arrival=a) for (n, b, a) in workload]

    # Henüz gelmeyenleri arrival'a göre sırala
    pending = sorted(procs, key=lambda p: (p.arrival, p.name))
    ready: List[Process] = []
    done: List[Process] = []

    t = 0
    running: Optional[Process] = None

    busy = 0  # CPU'nun gerçek iş yaptığı tick sayısı (IDLE/CS sayılmaz)
    ctx_switches = 0

    overhead_left = 0
    switch_pending = False  # “şu an bir switch sonrası yeni dispatch bekleniyor” bayrağı

    gantt: List[str] = []  # kısa özet için timeline (IDLE/CS/P1...)

    def admit(now: int) -> None:
        """arrival <= now olan pending prosesleri ready'e taşır."""
        nonlocal pending, ready
        while pending and pending[0].arrival <= now:
            p = pending.pop(0)
            p.last_ready = p.arrival  # ilk kez ready'e giriş zamanı
            ready.append(p)

    # “Sistemde iş var mı?” bitti mi döngüsü
    while pending or ready or running or overhead_left > 0:
        admit(t)

        # 1) Context switch overhead varsa CPU CS ile meşgul
        if overhead_left > 0:
            gantt.append("CS")
            t += 1
            overhead_left -= 1
            continue

        # 2) CPU boşsa ve ready'de birileri varsa dispatch et
        if running is None and ready:
            running = ready.pop(0)

            # WAIT hesabı: proses en son ready'e girdiği andan dispatch edildiği ana kadar bekler
            running.waiting += t - running.last_ready
            running.dispatches += 1

            # RESPONSE sadece ilk dispatch anında hesaplanır
            if running.start is None:
                running.start = t
                running.response = running.start - running.arrival

            # RR ise quantum yükle
            if algo == "RR":
                running.quantum_left = max(1, int(quantum))
            else:
                running.quantum_left = 0

            # context switch sayımı (bir önceki olay nedeniyle switch_pending olduysa)
            if switch_pending:
                ctx_switches += 1
                switch_pending = False

        # 3) Hâlâ running yoksa idle tick
        if running is None:
            gantt.append("IDLE")
            t += 1
            continue

        # 4) 1 tick çalıştır
        gantt.append(running.name)
        running.remaining -= 1
        busy += 1
        if algo == "RR":
            running.quantum_left -= 1

        t += 1
        admit(t)

        # 5) Bitme kontrolü
        if running.remaining <= 0:
            running.remaining = 0
            running.finish = t
            done.append(running)
            running = None

            # bir sonraki dispatch "switch sonrası" olacak
            switch_pending = True

            # overhead uygulanacak mı? (sistemde hâlâ iş varsa)
            if overhead > 0 and (pending or ready):
                overhead_left = overhead
            continue

        # 6) RR preemption: quantum bittiyse kuyruğa geri at
        if algo == "RR" and running.quantum_left <= 0:
            running.preemptions += 1

            # RR'de tekrar ready'e giriş anı “şimdi”dir
            running.last_ready = t

            ready.append(running)
            running = None

            switch_pending = True
            if overhead > 0 and (pending or ready):
                overhead_left = overhead
            continue

    # ---------------- metrikler (makale için) ----------------
    n_done = len(done)
    n = n_done if n_done > 0 else 1

    avg_wait = sum(p.waiting for p in done) / n
    avg_resp = sum((p.response if p.response is not None else 0) for p in done) / n
    avg_tat = sum(((p.finish - p.arrival) if p.finish is not None else 0) for p in done) / n

    util = (busy / t) if t > 0 else 0.0
    cs_time = sum(1 for x in gantt if x == "CS")
    idle_time = sum(1 for x in gantt if x == "IDLE")
    throughput = (n_done / t) if t > 0 else 0.0

    fairness_wait = jains_fairness([p.waiting for p in done])
    fairness_tat = jains_fairness([((p.finish - p.arrival) if p.finish is not None else 0) for p in done])

    per_process = []
    for p in done:
        tat = (p.finish - p.arrival) if p.finish is not None else None
        per_process.append({
            "name": p.name,
            "arrival": p.arrival,
            "burst": p.burst,
            "start": p.start,
            "finish": p.finish,
            "waiting": p.waiting,
            "response": p.response,
            "turnaround": tat,
            "dispatches": p.dispatches,
            "preemptions": p.preemptions,
        })

    return {
        "total_time": t,
        "busy_time": busy,
        "cpu_util": util,
        "ctx_switches": ctx_switches,
        "algo": algo,
        "quantum": quantum if algo == "RR" else None,
        "overhead": overhead,

        # Ek bilgiler
        "cs_time": cs_time,
        "idle_time": idle_time,
        "throughput": throughput,
        "avg_tat": avg_tat,
        "fairness_wait": fairness_wait,
        "fairness_tat": fairness_tat,

        "done": done,
        "per_process": per_process,
        "gantt": gantt,
        "avg_wait": avg_wait,
        "avg_resp": avg_resp,
    }


# ============================================================
#  UI APP
#  (UI yorumları minimal, algoritma yorumları heavy)
# ============================================================
class SmartSchedulerApp:
    COLORS = {
        "bg": "#F6F8FB",
        "card": "#FFFFFF",
        "border": "#E5E7EB",
        "text": "#111827",
        "muted": "#6B7280",
        "header": "#111827",
        "header_muted": "#9CA3AF",
        "primary": "#2563EB",
        "primary_dark": "#1D4ED8",
        "accent": "#F59E0B",
        "accent_dark": "#D97706",
        "success": "#10B981",
        "danger": "#EF4444",
        "slate": "#334155",
        "select": "#DBEAFE",
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CPU Scheduler: FCFS vs RR (Benchmark v5 - BatchFix)")
        self.root.geometry("1180x860")
        self.root.minsize(980, 720)
        self.root.configure(bg=self.COLORS["bg"])

        self._setup_style()

        # ---------------- SIM STATE ----------------
        # t: simülasyon zamanı (tick)
        self.time = 0

        # CPU'nun gerçek iş yaptığı tick sayısı (CS/IDLE sayılmaz)
        self.busy_ticks = 0

        # CS/IDLE tick sayaçları (UI sim için)
        self.cs_ticks = 0
        self.idle_ticks = 0

        # context switch sayacı (switch_pending sonrası bir dispatch olunca artar)
        self.ctx_switches = 0

        # context switch overhead uygulanıyorsa burada kalan tick
        self.overhead_left = 0

        # "bir olay (finish/preempt) oldu, bir sonraki dispatch switch sonrası sayılacak" bayrağı
        self.switch_pending = False

        # queues
        self.process_counter = 1
        self.running: Optional[Process] = None
        self.ready_queue: List[Process] = []
        self.pending: List[Process] = []      # arrival'ı gelmemiş olanlar
        self.terminated: List[Process] = []   # bitmiş olanlar

        # Compare için workload listesi (UI'de eklenen her proses buraya yazılır)
        self.workload_specs: List[Tuple[str, int, int]] = []

        self.setup_ui()

    # ---------------- style ----------------
    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=("Segoe UI", 10))

        style.configure("TNotebook", background=self.COLORS["card"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 8))

        style.configure(
            "Treeview",
            font=("Consolas", 10),
            rowheight=26,
            background=self.COLORS["card"],
            fieldbackground=self.COLORS["card"],
            foreground=self.COLORS["text"],
            borderwidth=0,
        )
        style.map(
            "Treeview",
            background=[("selected", self.COLORS["select"])],
            foreground=[("selected", self.COLORS["text"])],
        )
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

        # buttons
        style.configure("Primary.TButton", padding=(12, 8), background=self.COLORS["primary"], foreground="white", borderwidth=0)
        style.map("Primary.TButton", background=[("active", self.COLORS["primary_dark"])])

        style.configure("Accent.TButton", padding=(12, 8), background=self.COLORS["accent"], foreground="white", borderwidth=0)
        style.map("Accent.TButton", background=[("active", self.COLORS["accent_dark"])])

        style.configure("Success.TButton", padding=(12, 8), background=self.COLORS["success"], foreground="white", borderwidth=0)
        style.map("Success.TButton", background=[("active", self.COLORS["success"])])

        style.configure("Slate.TButton", padding=(12, 8), background=self.COLORS["slate"], foreground="white", borderwidth=0)
        style.map("Slate.TButton", background=[("active", self.COLORS["slate"])])

        style.configure("Danger.TButton", padding=(12, 8), background=self.COLORS["danger"], foreground="white", borderwidth=0)
        style.map("Danger.TButton", background=[("active", self.COLORS["danger"])])

    # ---------------- UI ----------------
    def setup_ui(self) -> None:
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        header = tk.Frame(self.root, bg=self.COLORS["header"], padx=18, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(
            header,
            text="CPU Scheduling Simülasyonu (FCFS vs Round Robin)",
            font=("Segoe UI", 20, "bold"),
            fg="white",
            bg=self.COLORS["header"],
        ).pack(anchor="w")
        tk.Label(
            header,
            text="1 tick = 1 saniye • Auto-dispatch • Arrival • Context Switch Overhead • Metrikler • Compare",
            font=("Segoe UI", 10),
            fg=self.COLORS["header_muted"],
            bg=self.COLORS["header"],
        ).pack(anchor="w", pady=(4, 0))

        container = tk.Frame(self.root, bg=self.COLORS["bg"], padx=18, pady=14)
        container.grid(row=1, column=0, sticky="nsew")
        container.grid_rowconfigure(1, weight=1)
        container.grid_columnconfigure(0, weight=1)

        # ---- SETTINGS ----
        settings = self._card(container)
        settings.grid(row=0, column=0, sticky="ew", pady=(0, 12))   
        settings.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        # Algoritma seçimi
        algo_box = tk.Frame(settings, bg=self.COLORS["card"])
        algo_box.grid(row=0, column=0, sticky="w", padx=14, pady=12)
        tk.Label(algo_box, text="Algoritma", bg=self.COLORS["card"], fg=self.COLORS["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.algo_var = tk.StringVar(value="RR")
        rb_row = tk.Frame(algo_box, bg=self.COLORS["card"])
        rb_row.pack(anchor="w", pady=(8, 0))
        ttk.Radiobutton(rb_row, text="Round Robin", variable=self.algo_var, value="RR", command=self.update_ui_state).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(rb_row, text="FCFS", variable=self.algo_var, value="FCFS", command=self.update_ui_state).pack(side=tk.LEFT)

        # Quantum (RR)
        q_box = tk.Frame(settings, bg=self.COLORS["card"])
        q_box.grid(row=0, column=1, sticky="w", padx=14, pady=12)
        tk.Label(q_box, text="Quantum (RR)", bg=self.COLORS["card"], fg=self.COLORS["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        q_row = tk.Frame(q_box, bg=self.COLORS["card"])
        q_row.pack(anchor="w", pady=(8, 0))
        self.quantum_var = tk.StringVar(value="4")
        self.spin_quantum = ttk.Spinbox(q_row, from_=1, to=20, width=6, textvariable=self.quantum_var, command=self.update_cpu_visuals)
        self.spin_quantum.pack(side=tk.LEFT)
        ttk.Label(q_row, text="tick").pack(side=tk.LEFT, padx=(6, 0))

        # Arrival spread (random süreç eklerken arrival'ı ileri atmak için)
        a_box = tk.Frame(settings, bg=self.COLORS["card"])
        a_box.grid(row=0, column=2, sticky="w", padx=14, pady=12)
        tk.Label(a_box, text="Geliş Yayılımı (random)", bg=self.COLORS["card"], fg=self.COLORS["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        a_row = tk.Frame(a_box, bg=self.COLORS["card"])
        a_row.pack(anchor="w", pady=(8, 0))
        self.arrival_spread_var = tk.StringVar(value="0")
        self.spin_arrival = ttk.Spinbox(a_row, from_=0, to=10, width=6, textvariable=self.arrival_spread_var)
        self.spin_arrival.pack(side=tk.LEFT)
        ttk.Label(a_row, text="max").pack(side=tk.LEFT, padx=(6, 0))

        # Context switch overhead
        o_box = tk.Frame(settings, bg=self.COLORS["card"])
        o_box.grid(row=0, column=3, sticky="w", padx=14, pady=12)
        tk.Label(o_box, text="Switch Overhead", bg=self.COLORS["card"], fg=self.COLORS["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        o_row = tk.Frame(o_box, bg=self.COLORS["card"])
        o_row.pack(anchor="w", pady=(8, 0))
        self.overhead_var = tk.StringVar(value="0")
        self.spin_over = ttk.Spinbox(o_row, from_=0, to=3, width=6, textvariable=self.overhead_var)
        self.spin_over.pack(side=tk.LEFT)
        ttk.Label(o_row, text="tick").pack(side=tk.LEFT, padx=(6, 0))

        # Seed
        s_box = tk.Frame(settings, bg=self.COLORS["card"])
        s_box.grid(row=0, column=4, sticky="e", padx=14, pady=12)
        tk.Label(s_box, text="Seed", bg=self.COLORS["card"], fg=self.COLORS["text"], font=("Segoe UI", 10, "bold")).pack(anchor="e")
        s_row = tk.Frame(s_box, bg=self.COLORS["card"])
        s_row.pack(anchor="e", pady=(8, 0))
        self.ent_seed = ttk.Entry(s_row, width=10)
        self.ent_seed.insert(0, "42")
        self.ent_seed.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(s_row, text="Uygula", style="Slate.TButton", command=self.apply_seed).pack(side=tk.LEFT)

        # ---- BODY ----
        body = tk.Frame(container, bg=self.COLORS["bg"])
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure((0, 1, 2), weight=1, uniform="cols")

        # LEFT: READY/PENDING
        left = self._card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        tk.Label(left, text="Kuyruklar", bg=self.COLORS["card"], fg=self.COLORS["text"], font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(12, 6))
        self.nb_queues = ttk.Notebook(left)
        self.nb_queues.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        tab_ready = tk.Frame(self.nb_queues, bg=self.COLORS["card"])
        tab_pending = tk.Frame(self.nb_queues, bg=self.COLORS["card"])
        self.nb_queues.add(tab_ready, text="READY")
        self.nb_queues.add(tab_pending, text="PENDING")

        self.tree_ready = self._make_tree(
            tab_ready,
            columns=("name", "arrival", "burst", "remaining"),
            headings=("Süreç", "Arr", "Burst", "Kalan"),
        )
        self.tree_pending = self._make_tree(
            tab_pending,
            columns=("name", "arrival", "burst"),
            headings=("Süreç", "Arr", "Burst"),
        )

        # CENTER: CPU durum
        center = self._card(body)
        center.grid(row=0, column=1, sticky="nsew", padx=10)
        tk.Label(center, text="CPU", bg=self.COLORS["card"], fg=self.COLORS["text"], font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(12, 6))

        cpu_inner = tk.Frame(center, bg=self.COLORS["card"])
        cpu_inner.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))

        self.lbl_cpu_name = tk.Label(cpu_inner, text="[ BOŞ ]", font=("Segoe UI", 26, "bold"), bg=self.COLORS["card"], fg=self.COLORS["success"])
        self.lbl_cpu_name.pack(pady=(26, 6))
        self.lbl_cpu_time = tk.Label(cpu_inner, text="", font=("Segoe UI", 12), bg=self.COLORS["card"], fg=self.COLORS["muted"])
        self.lbl_cpu_time.pack(pady=(0, 16))

        self.progress = ttk.Progressbar(cpu_inner, length=260, mode="determinate")
        self.progress.pack(pady=(0, 18))

        stats = tk.Frame(cpu_inner, bg=self.COLORS["card"])
        stats.pack(fill=tk.X, pady=(10, 0))
        self.lbl_time = tk.Label(stats, text="t=0", font=("Segoe UI", 11, "bold"), bg=self.COLORS["card"], fg=self.COLORS["text"])
        self.lbl_time.grid(row=0, column=0, sticky="w")
        self.lbl_util = tk.Label(stats, text="CPU Util: 0.0%", font=("Segoe UI", 11), bg=self.COLORS["card"], fg=self.COLORS["text"])
        self.lbl_util.grid(row=0, column=1, sticky="w", padx=(16, 0))
        self.lbl_ctx = tk.Label(stats, text="CtxSwitch: 0", font=("Segoe UI", 11), bg=self.COLORS["card"], fg=self.COLORS["text"])
        self.lbl_ctx.grid(row=0, column=2, sticky="w", padx=(16, 0))
        stats.grid_columnconfigure((0, 1, 2), weight=1)

        # RIGHT: TERMINATED (Turn yok, sadece Wait/Resp)
        right = self._card(body)
        right.grid(row=0, column=2, sticky="nsew", padx=(10, 0))
        tk.Label(right, text="Çıkan Süreçler", bg=self.COLORS["card"], fg=self.COLORS["text"], font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(12, 6))

        self.tree_term = self._make_tree(
            right,
            columns=("name", "arr", "burst", "start", "finish", "wait", "resp", "tat"),
            headings=("Süreç", "Arr", "Burst", "Start", "Finish", "Wait", "Resp", "TAT"),
            pack_kwargs={"fill": tk.BOTH, "expand": True, "padx": 12, "pady": (0, 12)},
        )

        # ---- CONTROLS ----
        controls = self._card(container)
        controls.grid(row=2, column=0, sticky="ew", pady=(12, 12))
        controls.grid_columnconfigure(0, weight=1)

        btn_row = tk.Frame(controls, bg=self.COLORS["card"])
        btn_row.pack(fill=tk.X, padx=12, pady=12)

        ttk.Button(btn_row, text="➕ Rastgele Süreç", style="Primary.TButton", command=self.add_process_random).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="✍ Elle Süreç", style="Primary.TButton", command=self.add_process_manual).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Separator(btn_row, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(btn_row, text="⏱ 1 Tick", style="Accent.TButton", command=self.run_time_step).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="⏩ 10 Tick", style="Accent.TButton", command=lambda: self.run_n_ticks(10)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="⏭ Sonuna Kadar", style="Accent.TButton", command=self.run_to_completion).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Separator(btn_row, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(btn_row, text="📊 Metrikler", style="Success.TButton", command=self.show_metrics).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="⚖ RR vs FCFS", style="Slate.TButton", command=self.compare).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="🧪 Batch Deney", style="Slate.TButton", command=self.open_batch_dialog).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="🧹 Reset", style="Danger.TButton", command=self.reset_all).pack(side=tk.RIGHT)

        # ---- LOG ----
        log_card = self._card(container)
        log_card.grid(row=3, column=0, sticky="nsew")
        tk.Label(log_card, text="Log", bg=self.COLORS["card"], fg=self.COLORS["text"], font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(12, 6))

        log_wrap = tk.Frame(log_card, bg=self.COLORS["card"])
        log_wrap.pack(fill=tk.BOTH, expand=False, padx=12, pady=(0, 12))

        self.log_box = tk.Text(
            log_wrap,
            height=7,
            font=("Consolas", 9),
            bg="#0B1220",
            fg="#E5E7EB",
            insertbackground="#E5E7EB",
            relief="flat",
            padx=10,
            pady=8,
            state="disabled",
        )
        scr = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scr.set)
        self.log_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scr.pack(side=tk.RIGHT, fill=tk.Y)

        # Initial renders
        self.update_ui_state()
        self.update_cpu_visuals()
        self.render_ready_queue()
        self.render_pending()
        self.render_terminated()

        # Keybinds
        self.root.bind("<space>", lambda _e: self.run_time_step())
        self.root.bind("<Control-r>", lambda _e: self.reset_all())

    def _card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=self.COLORS["card"],
            highlightbackground=self.COLORS["border"],
            highlightthickness=1,
            bd=0,
        )

    def _make_tree(
        self,
        parent: tk.Misc,
        columns: Tuple[str, ...],
        headings: Tuple[str, ...],
        pack_kwargs: Optional[Dict] = None,
    ) -> ttk.Treeview:
        frame = parent if isinstance(parent, tk.Frame) else tk.Frame(parent)

        tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        for col, head in zip(columns, headings):
            tree.heading(col, text=head)
            tree.column(col, anchor="center", width=80)

        if columns:
            tree.column(columns[0], width=140, anchor="w")

        scr = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scr.set)

        if pack_kwargs is None:
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scr.pack(side=tk.RIGHT, fill=tk.Y)
        else:
            tree.pack(side=tk.LEFT, **pack_kwargs)
            scr.pack(side=tk.RIGHT, fill=tk.Y)

        return tree

    # ---------------- helpers ----------------
    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.config(state="normal")
        self.log_box.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_box.see(tk.END)
        self.log_box.config(state="disabled")

    def apply_seed(self) -> None:
        s = self.ent_seed.get().strip()
        if not s:
            return
        try:
            random.seed(int(s))
            self.log(f"Seed set: {s}")
        except ValueError:
            messagebox.showwarning("Hata", "Seed sayı olmalı.")

    def _get_int(self, var: tk.StringVar, fallback: int) -> int:
        try:
            return int(var.get())
        except ValueError:
            return fallback

    def update_ui_state(self) -> None:
        """
        FCFS seçilince quantum irrelevant -> disabled
        RR seçilince quantum aktif
        """
        if self.algo_var.get() == "FCFS":
            self.spin_quantum.state(["disabled"])
            self.log("Algoritma: FCFS (preemption yok).")
        else:
            self.spin_quantum.state(["!disabled"])
            self.log("Algoritma: Round Robin (quantum ile preempt).")
        self.update_cpu_visuals()

    # ---------------- render tables ----------------
    def render_ready_queue(self) -> None:
        self.tree_ready.delete(*self.tree_ready.get_children())
        for p in self.ready_queue:
            self.tree_ready.insert("", "end", values=(p.name, p.arrival, p.burst, p.remaining))

    def render_pending(self) -> None:
        self.tree_pending.delete(*self.tree_pending.get_children())
        for p in self.pending:
            self.tree_pending.insert("", "end", values=(p.name, p.arrival, p.burst))


    def render_terminated(self) -> None:
        self.tree_term.delete(*self.tree_term.get_children())
        # newest on top (reverse)
        for p in reversed(self.terminated):
            rsp = p.response if p.response is not None else "-"
            tat = (p.finish - p.arrival) if p.finish is not None else "-"
            self.tree_term.insert(
                "",
                "end",
                values=(p.name, p.arrival, p.burst, p.start, p.finish, p.waiting, rsp, tat),
            )
    def update_cpu_visuals(self) -> None:
        """
        CPU panelini günceller:
        - running varsa kalan süre ve (RR ise) quantum left gösterir
        - overhead varsa SWITCH gösterir
        - yoksa BOŞ gösterir
        """
        p = self.running
        if p:
            self.lbl_cpu_name.config(text=f"[ {p.name} ]", fg=self.COLORS["danger"])
            qtxt = f" • q_left:{p.quantum_left}" if self.algo_var.get() == "RR" else ""
            self.lbl_cpu_time.config(text=f"Kalan: {p.remaining}s{qtxt}", fg=self.COLORS["muted"])
            prog = ((p.burst - p.remaining) / max(1, p.burst)) * 100
            self.progress["value"] = prog
        else:
            if self.overhead_left > 0:
                self.lbl_cpu_name.config(text="[ SWITCH ]", fg="#8B5CF6")
                self.lbl_cpu_time.config(text=f"Overhead: {self.overhead_left}s", fg=self.COLORS["muted"])
                self.progress["value"] = 0
            else:
                self.lbl_cpu_name.config(text="[ BOŞ ]", fg=self.COLORS["success"])
                self.lbl_cpu_time.config(text="", fg=self.COLORS["muted"])
                self.progress["value"] = 0

        self.lbl_time.config(text=f"t={self.time}")
        util = (self.busy_ticks / self.time) * 100 if self.time > 0 else 0.0
        self.lbl_util.config(text=f"CPU Util: {util:.1f}%")
        self.lbl_ctx.config(text=f"CtxSwitch: {self.ctx_switches}")

    # ---------------- process add (core) ----------------
    def _existing_names(self) -> set:
        return {name for (name, _, _) in self.workload_specs}

    def _add_process_object(self, p: Process, source: str = "NEW") -> bool:
        """
        Tek noktadan süreç ekleme:
        - compare için workload_specs'e de kaydet
        - arrival <= current time ise READY'ye koy
          yoksa PENDING'e koy (arrival'a göre sıralı tutuluyor)
        """
        if p.name in self._existing_names():
            messagebox.showwarning("Hata", f"{p.name} zaten var. Farklı isim gir.")
            return False

        self.workload_specs.append((p.name, p.burst, p.arrival))

        if p.arrival <= self.time:
            self.ready_queue.append(p)
            self.log(f"{source}: {p.name} burst={p.burst}s arrival=t{p.arrival} (READY)")
        else:
            self.pending.append(p)
            self.pending.sort(key=lambda x: (x.arrival, x.name))
            self.log(f"{source}: {p.name} burst={p.burst}s arrival=t{p.arrival} (PENDING)")

        self.render_ready_queue()
        self.render_pending()
        self.update_cpu_visuals()
        return True

    def add_process_random(self) -> None:
        """
        Rastgele proses:
        burst: 3..10
        arrival: şimdi + [0..spread] (spread=0 ise hepsi hemen gelir)
        """
        burst = random.randint(3, 10)
        spread = self._get_int(self.arrival_spread_var, 0)
        arrival = self.time + (random.randint(0, spread) if spread > 0 else 0)

        name = f"P{self.process_counter}"
        p = Process(name=name, burst=burst, arrival=arrival)

        ok = self._add_process_object(p, source="NEW(RANDOM)")
        if ok:
            self.process_counter += 1

    def add_process_manual(self) -> None:
        """Elle isim/burst/arrival girerek proses ekleme."""
        win = tk.Toplevel(self.root)
        win.title("Elle Süreç Ekle")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        frm = tk.Frame(win, padx=14, pady=14, bg=self.COLORS["bg"])
        frm.pack()

        card = self._card(frm)
        card.pack()

        inner = tk.Frame(card, bg=self.COLORS["card"], padx=14, pady=14)
        inner.pack()

        tk.Label(inner, text="İsim (P1 gibi):", bg=self.COLORS["card"], fg=self.COLORS["text"]).grid(row=0, column=0, sticky="w")
        ent_name = ttk.Entry(inner, width=22)
        ent_name.insert(0, f"P{self.process_counter}")
        ent_name.grid(row=0, column=1, padx=10, pady=6)

        tk.Label(inner, text="Burst (sn):", bg=self.COLORS["card"], fg=self.COLORS["text"]).grid(row=1, column=0, sticky="w")
        ent_burst = ttk.Entry(inner, width=22)
        ent_burst.insert(0, "5")
        ent_burst.grid(row=1, column=1, padx=10, pady=6)

        tk.Label(inner, text="Arrival (t):", bg=self.COLORS["card"], fg=self.COLORS["text"]).grid(row=2, column=0, sticky="w")
        ent_arr = ttk.Entry(inner, width=22)
        ent_arr.insert(0, str(self.time))
        ent_arr.grid(row=2, column=1, padx=10, pady=6)

        tk.Label(inner, text="Arrival mutlak zamandır. Örn: 0, 5, 10 ...", bg=self.COLORS["card"], fg=self.COLORS["muted"]).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 10)
        )

        def on_add():
            name = ent_name.get().strip()
            if not name:
                messagebox.showwarning("Hata", "İsim boş olamaz.")
                return

            try:
                burst = int(ent_burst.get().strip())
                arrival = int(ent_arr.get().strip())
            except ValueError:
                messagebox.showwarning("Hata", "Burst ve Arrival sayı olmalı.")
                return

            if burst <= 0:
                messagebox.showwarning("Hata", "Burst > 0 olmalı.")
                return
            if arrival < 0:
                messagebox.showwarning("Hata", "Arrival >= 0 olmalı.")
                return

            p = Process(name=name, burst=burst, arrival=arrival)
            ok = self._add_process_object(p, source="NEW(MANUAL)")
            if ok and name == f"P{self.process_counter}":
                self.process_counter += 1

            if ok:
                win.destroy()

        btns = tk.Frame(inner, bg=self.COLORS["card"])
        btns.grid(row=4, column=0, columnspan=2, pady=(4, 0), sticky="e")
        ttk.Button(btns, text="Ekle", style="Success.TButton", command=on_add).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="İptal", style="Slate.TButton", command=win.destroy).pack(side=tk.LEFT)

    # ============================================================
    #  ALGORİTMA TARAFI (UI tick sim)
    # ============================================================
    def admit_arrivals(self) -> None:
        """
        PENDING -> READY geçişi:
        arrival <= current_time olan tüm prosesleri READY'e al.
        """
        moved = 0
        while self.pending and self.pending[0].arrival <= self.time:
            p = self.pending.pop(0)
            p.last_ready = p.arrival
            self.ready_queue.append(p)
            moved += 1
            self.log(f"ARRIVE: {p.name} READY'ye girdi (t{self.time}).")

        if moved:
            self.render_ready_queue()
            self.render_pending()

    def dispatch_next(self) -> None:
        """
        CPU boşsa ve READY varsa bir proses seçer ve CPU'ya verir.
        Seçim kuralı:
          - Bu projede FCFS ve RR ikisi de FIFO ready kuyruğu kullanıyor.
          - Fark RR'de: quantum bitince proses tekrar kuyruğa atılır (preempt).
        """
        if self.running is not None or not self.ready_queue:
            return

        algo = self.algo_var.get()
        quantum = max(1, self._get_int(self.quantum_var, 4))

        p = self.ready_queue.pop(0)
        self.render_ready_queue()

        # WAIT hesabı:
        # Proses en son ne zaman ready'e girmişti?
        p.waiting += self.time - p.last_ready

        # RESP hesabı (ilk kez CPU'ya girince)
        if p.start is None:
            p.start = self.time
            p.response = p.start - p.arrival

        p.dispatches += 1
        p.quantum_left = quantum if algo == "RR" else 0

        # Context switch sayımı:
        # finish/preempt sonrası switch_pending True oluyordu,
        # ilk dispatch anında “bu switch gerçekleşti” diye sayıyoruz.
        if self.switch_pending:
            self.ctx_switches += 1
            self.switch_pending = False

        self.running = p
        self.log(f"DISPATCH: {p.name} CPU'ya alındı (t{self.time}).")
        self.update_cpu_visuals()

    def _schedule_overhead_if_needed(self) -> None:
        """
        Eğer overhead > 0 ise ve sistemde devam eden iş varsa
        (READY veya PENDING doluysa), CPU'ya CS tickleri ekle.
        """
        overhead = self._get_int(self.overhead_var, 0)
        if overhead <= 0:
            return
        if self.pending or self.ready_queue:
            self.overhead_left = overhead

    def run_time_step(self) -> None:
        """
        Simülasyonda 1 tick ilerletir.

        Sıra:
        1) arrival'ları READY'e al
        2) CS overhead varsa onu tüket (CPU o tickte CS ile meşgul)
        3) CPU boşsa dispatch yap
        4) hâlâ boşsa IDLE tick
        5) running varsa 1 tick çalıştır
           - finish oldu mu?
           - RR ise quantum bitti mi? (preempt)
        """
        # 1) arrival kontrolü
        self.admit_arrivals()

        # 2) context switch overhead (CPU bu tickte iş yapmıyor)
        if self.overhead_left > 0:
            self.log(f"CTXSW: overhead tick (kalan {self.overhead_left}s).")
            self.time += 1
            self.cs_ticks += 1
            self.overhead_left -= 1
            self.admit_arrivals()
            self.update_cpu_visuals()
            return

        # 3) CPU boşsa otomatik dispatch
        if self.running is None:
            self.dispatch_next()

        # 4) hâlâ boşsa (ready yok) -> IDLE tick
        if self.running is None:
            self.log(f"IDLE: CPU boş (t{self.time} -> t{self.time + 1}).")
            self.time += 1
            self.idle_ticks += 1
            self.admit_arrivals()
            self.update_cpu_visuals()
            return

        # 5) 1 tick work
        algo = self.algo_var.get()
        p = self.running

        self.log(f"RUN: {p.name} (t{self.time} -> t{self.time + 1}).")
        p.remaining -= 1
        self.busy_ticks += 1
        if algo == "RR":
            p.quantum_left -= 1

        self.time += 1
        self.admit_arrivals()

        # 5a) finish
        if p.remaining <= 0:
            p.remaining = 0
            p.finish = self.time

            self.terminated.append(p)
            self.running = None

            # bir sonraki dispatch “switch sonrası” sayılacak
            self.switch_pending = True

            self.log(f"EXIT: {p.name} bitti. finish=t{p.finish} wait={p.waiting} resp={p.response}")

            # overhead varsa uygula
            self._schedule_overhead_if_needed()

            self.render_terminated()
            self.update_cpu_visuals()
            return

        # 5b) RR quantum bitti -> preempt
        if algo == "RR" and p.quantum_left <= 0:
            p.preemptions += 1

            # tekrar ready'e giriş zamanı şimdi:
            p.last_ready = self.time

            # kuyruğun SONUNA at (RR FIFO)
            self.ready_queue.append(p)

            # CPU boşalır
            self.running = None

            self.switch_pending = True

            self.log(f"TIMEOUT: {p.name} quantum bitti, kuyruğa döndü (kalan {p.remaining}s).")

            self._schedule_overhead_if_needed()
            self.render_ready_queue()
            self.update_cpu_visuals()
            return

        self.update_cpu_visuals()

    def run_n_ticks(self, n: int) -> None:
        for _ in range(max(0, int(n))):
            self.run_time_step()
            self.root.update_idletasks()

    def run_to_completion(self) -> None:
        """
        Güvenlik limiti var.
        (Bazı bug'lı workload'larda sonsuz döngüye girmemesi için)
        """
        step_limit = 200000
        steps = 0
        while (self.pending or self.ready_queue or self.running or self.overhead_left > 0) and steps < step_limit:
            self.run_time_step()
            steps += 1
            if steps % 200 == 0:
                self.root.update_idletasks()
        if steps >= step_limit:
            messagebox.showwarning("Uyarı", "Step limiti doldu. (Çok büyük workload?)")

    # ---------------- reporting ----------------

    def show_metrics(self) -> None:
        """
        UI simülasyonu için metrikleri gösterir.
        (Compare ve Batch ayrı bir sim fonksiyonu ile çalışıyor.)
        """
        done = list(self.terminated)
        if not done:
            messagebox.showinfo("Metrikler", "Henüz tamamlanan süreç yok.")
            return

        n = len(done)

        avg_wait = sum(p.waiting for p in done) / n
        avg_resp = sum((p.response if p.response is not None else 0) for p in done) / n
        avg_tat = sum(((p.finish - p.arrival) if p.finish is not None else 0) for p in done) / n

        util = (self.busy_ticks / self.time) * 100 if self.time > 0 else 0.0
        throughput = (n / self.time) if self.time > 0 else 0.0

        fairness_wait = jains_fairness([p.waiting for p in done])
        fairness_tat = jains_fairness([((p.finish - p.arrival) if p.finish is not None else 0) for p in done])

        lines = [
            f"Total Time: {self.time}",
            f"Busy Time:  {self.busy_ticks}",
            f"CS Time:    {getattr(self, 'cs_ticks', 0)}",
            f"Idle Time:  {getattr(self, 'idle_ticks', 0)}",
            f"CPU Util:   {util:.1f}%",
            f"Throughput: {throughput:.4f} proses/tick",
            "",
            f"Context Switch (count): {self.ctx_switches}",
            "",
            f"Ortalama Waiting (READY bekleme): {avg_wait:.2f}",
            f"Ortalama Response (ilk dispatch): {avg_resp:.2f}",
            f"Ortalama Turnaround (finish-arrival): {avg_tat:.2f}",
            "",
            f"Fairness (Jain) - WAIT: {fairness_wait:.3f}",
            f"Fairness (Jain) - TAT : {fairness_tat:.3f}",
            "",
            "Süreç Bazlı:",
        ]
        for p in done:
            tat = (p.finish - p.arrival) if p.finish is not None else "-"
            lines.append(
                f"  {p.name}: arr={p.arrival} burst={p.burst} start={p.start} finish={p.finish} "
                f"wait={p.waiting} resp={p.response} tat={tat} preempt={p.preemptions}"
            )

        messagebox.showinfo("Metrikler", "\n".join(lines))
    # ---------------- batch deney (makale için) ----------------
    def open_batch_dialog(self) -> None:
        """Çoklu seed deney + grafik + hipotez testi çıktıları üretir."""
        win = tk.Toplevel(self.root)
        win.title("Batch Deney (Makale Çıktıları)")
        win.configure(bg=self.COLORS["bg"])
        win.resizable(False, False)

        pad = 12
        box = self._card(win)
        box.pack(fill=tk.BOTH, expand=True, padx=pad, pady=pad)

        tk.Label(
            box,
            text="Batch Deney: çoklu seed, CSV + grafik + hipotez testi",
            bg=self.COLORS["card"],
            fg=self.COLORS["text"],
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 8))

        form = tk.Frame(box, bg=self.COLORS["card"])
        form.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))
        form.grid_columnconfigure(1, weight=1)

        out_var = tk.StringVar(value=os.path.join(os.getcwd(), "results"))
        seeds_var = tk.StringVar(value="30")
        scenario_var = tk.StringVar(value="ALL")
        qtest_var = tk.StringVar(value=str(max(1, self._get_int(self.quantum_var, 4))))
        qlist_var = tk.StringVar(value="1,2,4,6,8,10")
        overhead_var = tk.StringVar(value=str(self._get_int(self.overhead_var, 0)))

        def row(r: int, label: str, var: tk.StringVar, width: int = 26, chooser: bool = False):
            tk.Label(form, text=label, bg=self.COLORS["card"], fg=self.COLORS["muted"], font=("Segoe UI", 10)).grid(
                row=r, column=0, sticky="w", pady=6
            )
            ent = tk.Entry(
                form,
                textvariable=var,
                width=width,
                bg="#0B1220",
                fg="#E5E7EB",
                insertbackground="#E5E7EB",
                relief="flat",
            )
            ent.grid(row=r, column=1, sticky="ew", pady=6, padx=(10, 0))
            if chooser:
                def pick():
                    d = filedialog.askdirectory(title="Çıktı klasörü seç", initialdir=os.getcwd())
                    if d:
                        var.set(d)
                ttk.Button(form, text="Seç", style="Slate.TButton", command=pick).grid(row=r, column=2, padx=(10, 0), pady=6)
            return ent

        row(0, "Çıktı klasörü", out_var, chooser=True)
        row(1, "Seed sayısı", seeds_var)

        tk.Label(form, text="Senaryo", bg=self.COLORS["card"], fg=self.COLORS["muted"], font=("Segoe UI", 10)).grid(
            row=2, column=0, sticky="w", pady=6
        )
        scenario_combo = ttk.Combobox(
            form,
            textvariable=scenario_var,
            values=["ALL", "S1_light", "S2_heavy", "S3_bursty"],
            state="readonly",
            width=24,
        )
        scenario_combo.grid(row=2, column=1, sticky="w", pady=6, padx=(10, 0))

        row(3, "RR test quantum (hipotez)", qtest_var)
        row(4, "Quantum list (sweep)", qlist_var)
        row(5, "Overhead", overhead_var)

        hint = (
            "Not:\n"
            "- 'RR test quantum' hipotez testinde kullanılan q değeridir.\n"
            "- 'Quantum list' q sweep grafiği için (örn: 1,2,4,6,8,10).\n"
            "- Çıktılar seçtiğin klasöre .csv ve .png olarak kaydedilir."
        )
        tk.Label(box, text=hint, bg=self.COLORS["card"], fg=self.COLORS["muted"], justify="left", font=("Segoe UI", 9)).pack(
            anchor="w", padx=14, pady=(0, 12)
        )

        btns = tk.Frame(box, bg=self.COLORS["card"])
        btns.pack(fill=tk.X, padx=14, pady=(0, 12))

        def run():
            try:
                out_dir = (out_var.get().strip() or "results")
                out_dir = os.path.abspath(out_dir)
                os.makedirs(out_dir, exist_ok=True)
                seeds = int(seeds_var.get())
                if seeds <= 0:
                    raise ValueError("Seed sayısı 1+ olmalı.")
                scenario = scenario_var.get()
                qtest = int(qtest_var.get())
                overhead = int(overhead_var.get())
                qlist = [int(x.strip()) for x in qlist_var.get().split(",") if x.strip()]
                if not qlist:
                    qlist = [1, 2, 4, 6, 8, 10]
                self.run_batch_experiment(out_dir, scenario=scenario, seeds=seeds, qtest=qtest, q_list=qlist, overhead=overhead)
                messagebox.showinfo("Batch Deney", f"Tamamlandı.\nÇıktılar: {out_dir}")
                win.destroy()
            except Exception as e:
                detail = traceback.format_exc()
                messagebox.showerror("Batch Deney Hatası", f"{e}\n\nDetay (traceback):\n{detail}")

        ttk.Button(btns, text="▶ Çalıştır", style="Success.TButton", command=run).pack(side=tk.LEFT)
        ttk.Button(btns, text="Kapat", style="Danger.TButton", command=win.destroy).pack(side=tk.RIGHT)

    def _generate_workload(self, scenario: str, seed: int) -> List[Tuple[str, int, int]]:
        """Makale için 3 temel senaryo üretir."""
        rng = random.Random(seed)

        if scenario == "S1_light":
            n = 12
            arrivals = [rng.randint(0, 18) for _ in range(n)]
            bursts = [rng.randint(2, 8) for _ in range(n)]
        elif scenario == "S2_heavy":
            n = 40
            arrivals = [rng.randint(0, 25) for _ in range(n)]
            bursts = [rng.randint(1, 12) for _ in range(n)]
        else:  # S3_bursty
            n = 28
            arrivals = [rng.randint(0, 28) for _ in range(n)]
            bursts = []
            for _ in range(n):
                bursts.append(rng.randint(1, 4) if rng.random() < 0.7 else rng.randint(8, 20))

        workload = []
        for i in range(n):
            name = f"P{i+1}"
            workload.append((name, int(bursts[i]), int(arrivals[i])))

        workload.sort(key=lambda x: x[2])
        return workload

    def run_batch_experiment(self, out_dir: str, scenario: str, seeds: int, qtest: int, q_list: List[int], overhead: int) -> None:
        """Batch deney çalıştırır ve CSV + grafik + hipotez test çıktıları üretir."""
        os.makedirs(out_dir, exist_ok=True)

        scenarios = ["S1_light", "S2_heavy", "S3_bursty"] if scenario == "ALL" else [scenario]

        raw_rows: List[Dict] = []
        per_process_rows: List[Dict] = []
        sweep_rows: List[Dict] = []

        metrics = ["avg_resp", "avg_wait", "avg_tat", "throughput", "ctx_switches"]
        paired = {sc: {m: {"RR": [], "FCFS": []} for m in metrics} for sc in scenarios}

        for sc in scenarios:
            for s in range(seeds):
                wl = self._generate_workload(sc, seed=s)

                rr = simulate(wl, "RR", quantum=qtest, overhead=overhead)
                fc = simulate(wl, "FCFS", quantum=qtest, overhead=overhead)

                for res in (rr, fc):
                    raw_rows.append({
                        "scenario": sc,
                        "seed": s,
                        "algo": res["algo"],
                        "quantum": res.get("quantum"),
                        "overhead": res.get("overhead", overhead),
                        "total_time": res.get("total_time", 0),
                        "cpu_util": res.get("cpu_util", 0.0),
                        "busy_time": res.get("busy_time", 0),
                        "cs_time": res.get("cs_time", 0),
                        "idle_time": res.get("idle_time", 0),
                        "ctx_switches": res.get("ctx_switches", 0),
                        "avg_wait": res.get("avg_wait", 0.0),
                        "avg_resp": res.get("avg_resp", 0.0),
                        "avg_tat": res.get("avg_tat", 0.0),
                        "throughput": res.get("throughput", 0.0),
                        "fairness_wait": res.get("fairness_wait", 0.0),
                        "fairness_tat": res.get("fairness_tat", 0.0),
                    })

                    for pp in res.get("per_process", []):
                        per_process_rows.append({
                            "scenario": sc,
                            "seed": s,
                            "algo": res["algo"],
                            "process": pp.get("name"),
                            "arrival": pp.get("arrival"),
                            "burst": pp.get("burst"),
                            "start": pp.get("start"),
                            "finish": pp.get("finish"),
                            "waiting": pp.get("waiting"),
                            "response": pp.get("response"),
                            "turnaround": pp.get("turnaround"),
                        })

                for m in metrics:
                    paired[sc][m]["RR"].append(_safe_float(rr.get(m, 0.0)))
                    paired[sc][m]["FCFS"].append(_safe_float(fc.get(m, 0.0)))

                for q in q_list:
                    rrq = simulate(wl, "RR", quantum=int(q), overhead=overhead)
                    sweep_rows.append({
                        "scenario": sc,
                        "seed": s,
                        "quantum": int(q),
                        "overhead": overhead,
                        "avg_resp": rrq.get("avg_resp", 0.0),
                        "avg_wait": rrq.get("avg_wait", 0.0),
                        "avg_tat": rrq.get("avg_tat", 0.0),
                        "ctx_switches": rrq.get("ctx_switches", 0),
                        "throughput": rrq.get("throughput", 0.0),
                        "cpu_util": rrq.get("cpu_util", 0.0),
                    })

        def write_csv(path: str, rows: List[Dict]) -> None:
            if not rows:
                return
            keys = sorted({k for r in rows for k in r.keys()})
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(rows)

        write_csv(os.path.join(out_dir, "results_raw.csv"), raw_rows)
        write_csv(os.path.join(out_dir, "per_process.csv"), per_process_rows)
        write_csv(os.path.join(out_dir, "quantum_sweep_raw.csv"), sweep_rows)

        # summary (mean±SEM)
        summary_rows: List[Dict] = []
        grouped: Dict[Tuple[str, str], List[Dict]] = {}
        for r in raw_rows:
            grouped.setdefault((r["scenario"], r["algo"]), []).append(r)

        for (sc, algo), rows in grouped.items():
            out = {"scenario": sc, "algo": algo, "n": len(rows)}
            for m in ["avg_wait", "avg_resp", "avg_tat", "cpu_util", "ctx_switches", "throughput", "cs_time", "idle_time"]:
                mu, se = mean_sem([_safe_float(x.get(m, 0.0)) for x in rows])
                out[f"{m}_mean"] = mu
                out[f"{m}_sem"] = se
            summary_rows.append(out)

        write_csv(os.path.join(out_dir, "results_summary.csv"), summary_rows)

        # hypothesis tests (paired)
        hyp_rows: List[Dict] = []

        try:
            from scipy import stats  # type: ignore
            have_scipy = True
        except Exception:
            stats = None
            have_scipy = False

        def normal_2sided_p(z: float) -> float:
            z = abs(z)
            cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
            return 2.0 * (1.0 - cdf)

        for sc in scenarios:
            for m in metrics:
                rr_vals = paired[sc][m]["RR"]
                fc_vals = paired[sc][m]["FCFS"]
                n = len(rr_vals)

                diffs = [rr_vals[i] - fc_vals[i] for i in range(n)]
                mean_diff = statistics.mean(diffs) if diffs else 0.0
                sd_diff = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
                cohen_d = (mean_diff / sd_diff) if sd_diff > 0 else 0.0

                if have_scipy:
                    t_stat, p_t = stats.ttest_rel(rr_vals, fc_vals)  # type: ignore
                    tcrit = stats.t.ppf(0.975, df=max(1, n - 1))  # type: ignore
                else:
                    t_stat = (mean_diff / (sd_diff / math.sqrt(n))) if (sd_diff > 0 and n > 1) else 0.0
                    p_t = normal_2sided_p(t_stat)  # approx
                    tcrit = 1.96  # approx

                ci_half = tcrit * (sd_diff / math.sqrt(n)) if (sd_diff > 0 and n > 1) else 0.0
                ci_low, ci_high = mean_diff - ci_half, mean_diff + ci_half

                hyp_rows.append({
                    "scenario": sc,
                    "metric": m,
                    "n": n,
                    "RR_mean": statistics.mean(rr_vals) if rr_vals else 0.0,
                    "FCFS_mean": statistics.mean(fc_vals) if fc_vals else 0.0,
                    "mean_diff_RR_minus_FCFS": mean_diff,
                    "t_stat": float(t_stat),
                    "p_value": float(p_t),
                    "cohen_d": float(cohen_d),
                    "ci95_low": float(ci_low),
                    "ci95_high": float(ci_high),
                    "method": "scipy_ttest_rel" if have_scipy else "normal_approx_t",
                })

        write_csv(os.path.join(out_dir, "hypothesis_tests.csv"), hyp_rows)

        # report
        rep = []
        rep.append("ALGORİTMA KIYAS - BATCH RAPORU")
        rep.append(f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        rep.append(f"Scenario: {scenario} | seeds={seeds} | qtest={qtest} | overhead={overhead} | q_list={q_list}")
        rep.append("")
        rep.append("Özet: results_summary.csv")
        rep.append("Hipotez: hypothesis_tests.csv")
        rep.append("Quantum sweep: quantum_sweep_raw.csv")
        rep.append("Dağılım: per_process.csv (boxplot için)")
        rep.append("")
        rep.append("Not: SciPy yoksa p-value normal approx ile hesaplanır.")
        with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(rep))

        # plots
        try:
            import matplotlib.pyplot as plt  # type: ignore

            summary_map = {(r["scenario"], r["algo"]): r for r in summary_rows}
            metrics_bar = ["avg_resp", "avg_wait", "avg_tat", "ctx_switches", "throughput", "cpu_util"]

            for m in metrics_bar:
                fig = plt.figure()
                ax = fig.add_subplot(111)

                labels = scenarios
                rr_means, fc_means, rr_sems, fc_sems = [], [], [], []

                for sc in labels:
                    rr = summary_map.get((sc, "RR"), {})
                    fc = summary_map.get((sc, "FCFS"), {})
                    rr_means.append(_safe_float(rr.get(f"{m}_mean", 0.0)))
                    fc_means.append(_safe_float(fc.get(f"{m}_mean", 0.0)))
                    rr_sems.append(_safe_float(rr.get(f"{m}_sem", 0.0)))
                    fc_sems.append(_safe_float(fc.get(f"{m}_sem", 0.0)))

                x = list(range(len(labels)))
                width = 0.35
                ax.bar([i - width/2 for i in x], rr_means, width, yerr=rr_sems, label="RR")
                ax.bar([i + width/2 for i in x], fc_means, width, yerr=fc_sems, label="FCFS")
                ax.set_xticks(x)
                ax.set_xticklabels(labels)
                ax.set_title(f"{m} (mean±SEM)")
                ax.legend()
                fig.tight_layout()
                fig.savefig(os.path.join(out_dir, f"bar_{m}.png"), dpi=160)
                plt.close(fig)

            # quantum sweep line
            for sc in scenarios:
                sc_rows = [r for r in sweep_rows if r["scenario"] == sc]
                if not sc_rows:
                    continue
                qvals = sorted({int(r["quantum"]) for r in sc_rows})

                def mean_for(metric: str):
                    out = []
                    for q in qvals:
                        vals = [_safe_float(r.get(metric, 0.0)) for r in sc_rows if int(r["quantum"]) == q]
                        out.append(statistics.mean(vals) if vals else 0.0)
                    return out

                for metric in ["avg_resp", "ctx_switches"]:
                    fig = plt.figure()
                    ax = fig.add_subplot(111)
                    ax.plot(qvals, mean_for(metric), marker="o")
                    ax.set_title(f"{sc} - quantum sweep ({metric})")
                    ax.set_xlabel("quantum")
                    ax.set_ylabel(metric)
                    fig.tight_layout()
                    fig.savefig(os.path.join(out_dir, f"line_{sc}_{metric}.png"), dpi=160)
                    plt.close(fig)

            # boxplot waiting
            for sc in scenarios:
                rr_w = [_safe_float(r["waiting"]) for r in per_process_rows if r["scenario"] == sc and r["algo"] == "RR"]
                fc_w = [_safe_float(r["waiting"]) for r in per_process_rows if r["scenario"] == sc and r["algo"] == "FCFS"]
                if not rr_w or not fc_w:
                    continue
                fig = plt.figure()
                ax = fig.add_subplot(111)
                ax.boxplot([rr_w, fc_w], labels=["RR", "FCFS"])
                ax.set_title(f"{sc} - Waiting dağılımı (boxplot)")
                ax.set_ylabel("waiting")
                fig.tight_layout()
                fig.savefig(os.path.join(out_dir, f"box_{sc}_waiting.png"), dpi=160)
                plt.close(fig)

        except Exception:
            pass

    def compare(self) -> None:
        """
        Aynı workload'u RR ve FCFS ile ayrı ayrı simüle eder.
        Not: Bu compare UI akışından bağımsızdır -> simulate() kullanır.
        """
        if not self.workload_specs:
            messagebox.showinfo("Karşılaştır", "Önce birkaç süreç ekle.")
            return

        quantum = max(1, self._get_int(self.quantum_var, 4))
        overhead = self._get_int(self.overhead_var, 0)

        rr = simulate(self.workload_specs, "RR", quantum=quantum, overhead=overhead)
        fcfs = simulate(self.workload_specs, "FCFS", quantum=quantum, overhead=overhead)

        def fmt_block(res: Dict) -> str:
            util = res["cpu_util"] * 100
            return (
                f"Total Time: {res['total_time']}\n"
                f"CPU Util: {util:.1f}%\n"
                f"Avg Wait: {res['avg_wait']:.2f}\n"
                f"Avg Resp: {res['avg_resp']:.2f}\n"
                f"Avg TAT : {res.get('avg_tat', 0.0):.2f}\n"
                f"Through : {res.get('throughput', 0.0):.4f} proses/tick\n"
                f"Fair(W) : {res.get('fairness_wait', 0.0):.3f}\n"
                f"Fair(T) : {res.get('fairness_tat', 0.0):.3f}\n"
                f"CS Time : {res.get('cs_time', 0)}\n"
                f"Idle    : {res.get('idle_time', 0)}\n"
                f"CtxSw#  : {res['ctx_switches']}\n"
            )

        def gantt_preview(g: List[str]) -> str:
            if len(g) <= 80:
                return " ".join(g)
            head = " ".join(g[:40])
            tail = " ".join(g[-40:])
            return f"{head} ... {tail}"

        msg = [
            "Aynı workload üzerinde adil kıyas:",
            "",
            f"RR (quantum={quantum}, overhead={overhead})",
            fmt_block(rr),
            "FCFS",
            fmt_block(fcfs),
            "",
            "Gantt (özet):",
            f"RR:   {gantt_preview(rr['gantt'])}",
            f"FCFS: {gantt_preview(fcfs['gantt'])}",
        ]

        messagebox.showinfo("RR vs FCFS", "\n".join(msg))

    # ---------------- reset ----------------
    def reset_all(self) -> None:
        """Sim state + tablolar + log sıfırlama."""
        self.time = 0
        self.busy_ticks = 0
        self.cs_ticks = 0
        self.idle_ticks = 0
        self.ctx_switches = 0
        self.overhead_left = 0
        self.switch_pending = False

        self.process_counter = 1
        self.running = None
        self.ready_queue.clear()
        self.pending.clear()
        self.terminated.clear()
        self.workload_specs.clear()

        self.tree_ready.delete(*self.tree_ready.get_children())
        self.tree_pending.delete(*self.tree_pending.get_children())
        self.tree_term.delete(*self.tree_term.get_children())

        self.log_box.config(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.config(state="disabled")

        self.update_cpu_visuals()
        self.log("Reset tamam.")


if __name__ == "__main__":
    root = tk.Tk()
    app = SmartSchedulerApp(root)
    root.mainloop()