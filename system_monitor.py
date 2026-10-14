#!/usr/bin/env python3
"""Диспетчер завдань (прототип) у стилі Windows Task Manager.

GTK4 + libadwaita + psutil. Дані з /proc, оновлення ~раз на секунду.
Запуск з --snapshot ФАЙЛ.png збереже знімок вікна і вийде.
"""
import math
import subprocess
import sys
import time
import warnings
from collections import deque
from datetime import timedelta

warnings.filterwarnings("ignore", category=DeprecationWarning)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk

import psutil

HISTORY = 60
CPU_COUNT = psutil.cpu_count(logical=True) or 1

WIN_CSS = """
* { font-family: "Segoe UI", "Noto Sans", "DejaVu Sans", sans-serif; }

window, .monitor-root { background: #f2f4f7; color: #16181d; }

.titlebar {
    background: #e7ebf1;
    border-bottom: 1px solid #c9d1da;
    min-height: 34px;
    padding: 2px 8px;
}
.titlebar .app-title { font-size: 12px; color: #1f2328; font-weight: 700; }

.toolbar {
    background: #e7ebf1;
    border-bottom: 1px solid #c9d1da;
    padding: 4px 10px;
}

.win-tabs button {
    background: transparent;
    border: none;
    border-bottom: 3px solid transparent;
    border-radius: 0;
    box-shadow: none;
    padding: 7px 16px;
    margin: 0;
    color: #495059;
    font-size: 13px;
}
.win-tabs button:hover { background: #d7dee7; color: #1f2328; }
.win-tabs button:checked {
    background: #ffffff;
    color: #0058a8;
    border-bottom: 3px solid #0067c0;
    font-weight: 700;
}

.tasks-view { background: #ffffff; }
.tasks-view:selected { background: #b5d7f2; color: #0a0a0a; }
.tasks-view header button {
    background: #dde4ec;
    color: #232830;
    border-radius: 0;
    border-right: 1px solid #c2cbd6;
    border-bottom: 1px solid #b9c3cf;
    box-shadow: none;
    font-weight: 700;
    font-size: 12px;
    padding: 6px 8px;
    min-height: 0;
}
.tasks-view header button:hover { background: #cdd6e0; }

frame, scrolledwindow, .tasks-frame { border: none; border-radius: 0; }

.kill-btn {
    background: #f7cfcf;
    color: #9d1616;
    border: 1px solid #e79b9b;
    border-radius: 4px;
    box-shadow: none;
    font-weight: 700;
    padding: 5px 12px;
}
.kill-btn:hover { background: #f2b6b6; }

.snap-btn {
    background: #cfe2f7;
    color: #0058a8;
    border: 1px solid #9cc0e6;
    border-radius: 4px;
    box-shadow: none;
    font-weight: 700;
    padding: 5px 12px;
}
.snap-btn:hover { background: #bcd7f2; }

.tool-btn {
    background: #e3e8ee;
    color: #2b3038;
    border: 1px solid #bcc6d2;
    border-radius: 4px;
    box-shadow: none;
    font-weight: 600;
    padding: 5px 12px;
}
.tool-btn:hover { background: #d3dbe4; }

.search-box entry {
    background: #ffffff;
    border: 1px solid #b9c3cf;
    border-radius: 4px;
    min-height: 26px;
    box-shadow: none;
}
.search-box entry:focus { border-color: #0067c0; }

.perf-title { font-weight: 700; font-size: 13px; color: #14161a; }
.perf-value { font-size: 12px; color: #3c424b; font-weight: 600; }

.perf-root { background: #ffffff; }
.perf-side { background: #e9edf2; }
.perf-side row { padding: 9px 14px; border-bottom: 1px solid #dde3ea; }
.perf-side row:selected { background: #cfe2f7; }
.perf-side-label { font-size: 13px; color: #232830; font-weight: 600; }
.perf-side row:selected .perf-side-label { color: #0058a8; }
.perf-title-big { font-size: 15px; font-weight: 700; color: #14161a; }
.graph-frame { border: 1px solid #cfd6de; background: #ffffff; }
.stat-label { font-size: 12px; color: #5a616b; }
.stat-value {
    font-family: "DejaVu Sans Mono", "Noto Sans Mono", monospace;
    font-size: 12px;
    color: #14161a;
    font-weight: 700;
}
.core-label { font-size: 10px; color: #6a717b; }
.core-value {
    font-family: "DejaVu Sans Mono", "Noto Sans Mono", monospace;
    font-size: 11px;
    color: #14161a;
    font-weight: 700;
}
.core-cell {
    border: 1px solid #d3dae2;
    background: #fbfcfd;
    padding: 3px 6px;
    border-radius: 2px;
}
"""


def _cairo_ok():
    try:
        import cairo  # noqa: F401

        gi.require_foreign("cairo")
        return True
    except Exception:
        return False


CAIRO_OK = _cairo_ok()

STATUS_UK = {
    "running": "виконується",
    "sleeping": "очікування",
    "disk-sleep": "сон (диск)",
    "stopped": "зупинено",
    "tracing-stop": "трасування",
    "zombie": "зомбі",
    "dead": "мертвий",
    "waking": "прокидається",
    "idle": "неактивний",
    "locked": "заблоковано",
    "waiting": "очікування",
    "suspended": "призупинено",
    "parked": "припарковано",
}

# --------------------------------------------------------------------- heat
_HEAT_STOPS = [
    (0.00, (255, 250, 232)),
    (0.15, (255, 233, 138)),
    (0.35, (255, 205, 60)),
    (0.55, (252, 160, 28)),
    (0.75, (238, 100, 24)),
    (1.00, (198, 24, 24)),
]
_heat_cache = {}
_TRANSPARENT = Gdk.RGBA()


def heat(frac):
    key = int(max(0.0, min(1.0, frac)) * 100)
    if key in _heat_cache:
        return _heat_cache[key]
    f = key / 100.0
    col = _HEAT_STOPS[-1][1]
    for (a, ca), (b, cb) in zip(_HEAT_STOPS, _HEAT_STOPS[1:]):
        if f <= b:
            t = 0.0 if b == a else (f - a) / (b - a)
            col = tuple(round(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))
            break
    rgba = Gdk.RGBA()
    rgba.red, rgba.green, rgba.blue, rgba.alpha = (
        col[0] / 255,
        col[1] / 255,
        col[2] / 255,
        1.0,
    )
    _heat_cache[key] = rgba
    return rgba


# -------------------------------------------------------------------- graphs
MARGIN_L = 48
MARGIN_R = 10
MARGIN_T = 10
MARGIN_B = 18


def nice_max(v):
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = 10 ** exp
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if v <= m * base:
            return m * base
    return 10 * base


def fmt_axis(v, ymax):
    if ymax >= 5:
        return f"{v:.0f}"
    if ymax >= 1:
        return f"{v:.1f}"
    return f"{v:.2f}"


class _Plot(Gtk.DrawingArea):
    """Спільна основа: сітка, осі з підписами, час."""

    def __init__(self, height, unit="%", axes=True):
        super().__init__()
        self.unit = unit
        self.show_axes = axes
        self.set_content_height(height)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)

    def _plot_rect(self, width, height):
        if self.show_axes:
            return (
                MARGIN_L,
                MARGIN_T,
                max(1, width - MARGIN_L - MARGIN_R),
                max(1, height - MARGIN_T - MARGIN_B),
            )
        return 4, 4, max(1, width - 8), max(1, height - 8)

    def _axes(self, cr, width, height, ylabels):
        x0, y0, pw, ph = self._plot_rect(width, height)
        cr.set_line_width(1.0)
        cr.set_source_rgba(0.42, 0.45, 0.50, 0.55)
        for i in range(5):
            x = x0 + pw * i / 4
            cr.move_to(x, y0)
            cr.line_to(x, y0 + ph)
        for i in range(5):
            y = y0 + ph * i / 4
            cr.move_to(x0, y)
            cr.line_to(x0 + pw, y)
        cr.stroke()

        if not self.show_axes:
            return
        cr.set_source_rgb(0.35, 0.39, 0.44)
        cr.select_font_face("sans", 0, 0)
        cr.set_font_size(10)
        for i, lab in enumerate(ylabels):
            y = y0 + ph * i / 4
            ext = cr.text_extents(lab)
            cr.move_to(x0 - 6 - ext.width, y + 3.5)
            cr.show_text(lab)
        for i, lab in enumerate(("60с", "45с", "30с", "15с", "0")):
            x = x0 + pw * i / 4
            ext = cr.text_extents(lab)
            cr.move_to(x - ext.width / 2, y0 + ph + 13)
            cr.show_text(lab)
        cr.set_source_rgb(0.45, 0.49, 0.54)
        ext = cr.text_extents(self.unit)
        cr.move_to(x0 + pw - ext.width, y0 + 10)
        cr.show_text(self.unit)
        return x0, y0, pw, ph


class CairoGraph(_Plot):
    """Відсотковий графік (0..100) з історією."""

    def __init__(self, color, height=170, axes=True):
        super().__init__(height, "%", axes)
        self.color = color
        self.series = deque([0.0] * HISTORY, maxlen=HISTORY)

    def push(self, value):
        self.series.append(max(0.0, min(100.0, value)))
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        self._axes(cr, width, height, ["100", "75", "50", "25", "0"])

        x0, y0, pw, ph = self._plot_rect(width, height)
        points = list(self.series)
        step = pw / (HISTORY - 1)
        r, g, b = self.color

        def py(v):
            return y0 + ph - (v / 100.0) * ph

        cr.set_source_rgba(r, g, b, 0.30)
        cr.move_to(x0, y0 + ph)
        for i, v in enumerate(points):
            cr.line_to(x0 + i * step, py(v))
        cr.line_to(x0 + (len(points) - 1) * step, y0 + ph)
        cr.close_path()
        cr.fill()

        cr.set_source_rgb(r, g, b)
        cr.set_line_width(2.0)
        for i, v in enumerate(points):
            pxx = x0 + i * step
            if i == 0:
                cr.move_to(pxx, py(v))
            else:
                cr.line_to(pxx, py(v))
        cr.stroke()


class RateGraph(_Plot):
    """Графік швидкості (МБ/с) з авто-масштабом, кілька ліній."""

    def __init__(self, colors, height=170, axes=True):
        super().__init__(height, "МБ/с", axes)
        self.colors = colors
        self.series = [deque([0.0] * HISTORY, maxlen=HISTORY) for _ in colors]

    def push(self, values):
        for s, v in zip(self.series, values):
            s.append(max(0.0, v))
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()

        peak = max((max(s) for s in self.series), default=0.0)
        scale = nice_max(peak * 1.15)
        ylabels = [fmt_axis(scale * (4 - i) / 4, scale) for i in range(5)]
        self._axes(cr, width, height, ylabels)

        x0, y0, pw, ph = self._plot_rect(width, height)
        step = pw / (HISTORY - 1)

        def py(v):
            return y0 + ph - min(1.0, v / scale) * ph

        for color, series in zip(self.colors, self.series):
            r, g, b = color
            points = list(series)
            cr.set_source_rgba(r, g, b, 0.28)
            cr.move_to(x0, y0 + ph)
            for i, v in enumerate(points):
                cr.line_to(x0 + i * step, py(v))
            cr.line_to(x0 + (len(points) - 1) * step, y0 + ph)
            cr.close_path()
            cr.fill()

            cr.set_source_rgb(r, g, b)
            cr.set_line_width(2.0)
            for i, v in enumerate(points):
                pxx = x0 + i * step
                if i == 0:
                    cr.move_to(pxx, py(v))
                else:
                    cr.line_to(pxx, py(v))
            cr.stroke()


class BarGraph(Gtk.Box):
    """Резервний режим без cairo."""

    def __init__(self, _color, height=170):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_hexpand(True)
        self.value_label = Gtk.Label(xalign=1)
        self.bar = Gtk.LevelBar()
        self.bar.set_min_value(0.0)
        self.bar.set_max_value(1.0)
        self.bar.set_vexpand(True)
        self.append(self.bar)
        self.append(self.value_label)

    def push(self, values):
        value = values if isinstance(values, (int, float)) else max(values, default=0)
        self.bar.set_value(max(0.0, min(1.0, value / 100.0)))
        self.value_label.set_text(f"{value:.1f}")


def make_graph(color, height=170, axes=True):
    return CairoGraph(color, height, axes) if CAIRO_OK else BarGraph(color, height)


def make_rate_graph(colors, height=170, axes=True):
    return RateGraph(colors, height, axes) if CAIRO_OK else BarGraph(colors[0], height)


CPU_COLOR = (0.0, 0.40, 0.90)
MEM_COLOR = (0.62, 0.20, 0.76)
DISK_READ = (0.10, 0.68, 0.35)
DISK_WRITE = (0.95, 0.45, 0.05)
NET_DOWN = (0.0, 0.42, 0.90)
NET_UP = (0.98, 0.50, 0.05)
GPU_COLOR = (0.16, 0.72, 0.35)


def fmt_uptime(seconds):
    td = timedelta(seconds=int(seconds))
    d, rem = divmod(td.seconds, 3600)
    h, m = divmod(rem, 60)
    return f"{td.days} д {d:02d}:{h:02d}:{m:02d}"


def fmt_bytes(n):
    if n >= 1024**3:
        return f"{n / 1024**3:.2f} ГБ"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} МБ"
    if n >= 1024:
        return f"{n / 1024:.0f} КБ"
    return f"{n:.0f} Б"


def active_iface():
    try:
        with open("/proc/net/route", encoding="utf-8") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 1 and parts[1] == "00000000":
                    return parts[0]
    except OSError:
        pass
    for name, st in psutil.net_if_stats().items():
        if name != "lo" and st.isup:
            return name
    return None


def gpu_info():
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        name, util, mused, mtotal, temp = [
            s.strip() for s in out.stdout.strip().splitlines()[0].split(",")
        ]
        return {
            "name": name,
            "util": float(util),
            "mem_used": float(mused),
            "mem_total": float(mtotal),
            "temp": float(temp),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _temps():
    try:
        return psutil.sensors_temperatures()
    except Exception:  # noqa: BLE001
        return {}


def cpu_temperature():
    st = _temps()
    prefs = {
        "k10temp": ("Tctl", "Tdie"),
        "zenpower": ("Tctl", "Tdie"),
        "coretemp": ("Package id 0", "Core 0"),
    }
    for chip in ("k10temp", "zenpower", "coretemp"):
        entries = st.get(chip)
        if not entries:
            continue
        for want in prefs[chip]:
            for e in entries:
                if e.label == want and e.current:
                    return e.current
        for e in entries:
            if e.current:
                return e.current
    for chip in ("cpu_thermal", "acpitz"):
        for e in st.get(chip, []):
            if e.current:
                return e.current
    return None


def disk_temperature():
    entries = _temps().get("nvme", [])
    for want in ("Composite", "Sensor 1"):
        for e in entries:
            if e.label == want and e.current:
                return e.current
    for e in entries:
        if e.current:
            return e.current
    return None


def ram_temperature():
    for chip, entries in _temps().items():
        for e in entries:
            label = (e.label or "").lower()
            if e.current and ("dimm" in label or "spd" in label):
                return e.current
        if chip.lower() in ("spd5118", "jc42"):
            for e in entries:
                if e.current:
                    return e.current
    return None


def fmt_temp(value):
    return f"{value:.0f} °C" if value else "н/д"


# --------------------------------------------------------------------- png
def save_png(widget, path):
    w = widget.get_width()
    h = widget.get_height()
    if w <= 0 or h <= 0:
        raise RuntimeError("вікно ще не має розміру")

    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, w, h)
    node = snapshot.to_node()
    if node is None:
        raise RuntimeError("вікно ще не намальоване")

    native = widget.get_native()
    surface = native.get_surface() if native else None
    if surface is None:
        raise RuntimeError("поверхня недоступна")

    renderer = Gsk.CairoRenderer.new()
    renderer.realize(surface)
    try:
        texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
    finally:
        renderer.unrealize()
    if texture is None:
        raise RuntimeError("не вдалося відрендерити вікно")
    texture.save_to_png(path)


# -------------------------------------------------------------------- window
class MonitorWindow(Adw.ApplicationWindow):
    def __init__(self, expand=False, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(940, 660)
        self.set_title("Диспетчер завдань")

        self.groups = {}  # назва групи -> Gtk.TreeIter рядка-групи
        self.leaves = {}  # pid -> (назва групи, Gtk.TreeIter)
        self.all_expanded = False
        self._start_expanded = expand
        self._did_expand = False
        self._popover = None
        self.total_mb = psutil.virtual_memory().total / 1024**2
        self._last_time = time.monotonic()
        self._last_disk = psutil.disk_io_counters()
        self.iface = active_iface()
        self._last_net = psutil.net_io_counters(pernic=True).get(self.iface)
        self._proc_count = 0
        self._thread_count = 0
        self._root_part = next(
            (p for p in psutil.disk_partitions() if p.mountpoint == "/"), None
        )

        self._build_actions()
        self.set_content(self._build_ui())

        self._tick()
        GLib.timeout_add(1000, self._tick)

    # -------------------------------------------------------------- layout
    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class("monitor-root")
        root.append(self._build_titlebar())

        stack = Gtk.Stack(hexpand=True, vexpand=True)
        stack.set_transition_type(Gtk.StackTransitionType.NONE)
        stack.add_titled(self._build_processes_page(), "proc", "Процеси")
        stack.add_titled(self._build_perf_page(), "perf", "Продуктивність")
        self.stack = stack

        root.append(self._build_toolbar(stack))
        root.append(stack)

        overlay = Adw.ToastOverlay()
        overlay.set_child(root)
        self.overlay = overlay
        return overlay

    def _build_titlebar(self):
        handle = Gtk.WindowHandle()
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("titlebar")

        icon = Gtk.Image.new_from_icon_name("utilities-system-monitor")
        icon.set_pixel_size(16)
        bar.append(icon)

        title = Gtk.Label(label="Диспетчер завдань", xalign=0)
        title.add_css_class("app-title")
        bar.append(title)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)

        bar.append(Gtk.WindowControls(side=Gtk.PackType.END))
        handle.set_child(bar)
        return handle

    def _build_toolbar(self, stack):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.add_css_class("toolbar")

        switcher = Gtk.StackSwitcher(stack=stack)
        switcher.add_css_class("win-tabs")
        switcher.set_halign(Gtk.Align.START)
        box.append(switcher)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        box.append(spacer)

        self.toggle_btn = Gtk.Button(label="Розгорнути все")
        self.toggle_btn.add_css_class("tool-btn")
        self.toggle_btn.connect("clicked", lambda *_: self._toggle_all())
        box.append(self.toggle_btn)

        self.search = Gtk.SearchEntry(placeholder_text="Пошук процесу…")
        self.search.set_width_chars(20)
        self.search.connect("search-changed", lambda *_: self._refresh_rows())
        self.search.add_css_class("search-box")
        box.append(self.search)

        snap_btn = Gtk.Button(label="Знімок")
        snap_btn.add_css_class("snap-btn")
        snap_btn.connect("clicked", lambda *_: self._save_snapshot())
        box.append(snap_btn)

        kill_btn = Gtk.Button(label="Завершити завдання")
        kill_btn.add_css_class("kill-btn")
        kill_btn.connect("clicked", lambda *_: self._kill_selected())
        box.append(kill_btn)
        return box

    def _build_processes_page(self):
        self.store = Gtk.TreeStore(int, str, str, float, float, str, bool)
        tree = Gtk.TreeView(model=self.store)
        tree.set_vexpand(True)
        tree.add_css_class("tasks-view")
        tree.set_show_expanders(True)
        tree.set_enable_tree_lines(False)
        tree.set_level_indentation(14)

        cols = [
            ("Ім'я", 1, 260, "text"),
            ("PID", 0, 80, "pid"),
            ("Статус", 5, 100, "text"),
            ("Користувач", 2, 120, "text"),
            ("CPU %", 3, 90, "cpu"),
            ("Пам'ять", 4, 120, "mem"),
        ]
        for title, idx, width, kind in cols:
            renderer = Gtk.CellRendererText()
            renderer.set_property("xpad", 8)
            if kind in ("cpu", "mem"):
                renderer.set_property("xalign", 1.0)
            if kind == "text":
                col = Gtk.TreeViewColumn(title, renderer, text=idx)
            else:
                col = Gtk.TreeViewColumn(title, renderer)
            col.set_sort_column_id(idx)
            col.set_fixed_width(width)
            col.set_resizable(True)
            col.set_cell_data_func(renderer, self._make_cell_func(kind))
            tree.append_column(col)

        self.store.set_sort_column_id(3, Gtk.SortType.DESCENDING)
        tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        tree.connect("row-activated", self._on_row_activated)
        self.tree = tree

        gesture = Gtk.GestureClick(button=3)
        gesture.connect("pressed", self._on_right_click)
        tree.add_controller(gesture)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(tree)
        scroll.add_css_class("tasks-frame")
        return scroll

    def _build_perf_page(self):
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        root.add_css_class("perf-root")

        self.perf_list = Gtk.ListBox()
        self.perf_list.add_css_class("perf-side")
        self.perf_list.set_selection_mode(Gtk.SelectionMode.SINGLE)

        side_scroll = Gtk.ScrolledWindow()
        side_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        side_scroll.set_child(self.perf_list)
        side_scroll.set_size_request(200, -1)

        self.perf_stack = Gtk.Stack()
        self.perf_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self.perf = {}
        self.perf_rows = {}
        components = [
            ("cpu", "Процесор", self._make_cpu_page),
            ("mem", "Пам'ять", self._make_mem_page),
            ("disk", "Диск", self._make_disk_page),
            ("net", "Езернет", self._make_net_page),
            ("gpu", "Графічний процесор", self._make_gpu_page),
        ]
        for key, label, builder in components:
            row = Gtk.ListBoxRow()
            lab = Gtk.Label(label=label, xalign=0)
            lab.add_css_class("perf-side-label")
            row.set_child(lab)
            row.perf_key = key
            self.perf_list.append(row)
            self.perf_rows[key] = row
            self.perf_stack.add_named(builder(), key)

        self.perf_list.connect("row-selected", self._on_perf_selected)
        self.perf_list.select_row(self.perf_list.get_row_at_index(0))

        root.append(side_scroll)
        root.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        root.append(self.perf_stack)
        return root

    def _on_perf_selected(self, _list, row):
        if row is not None:
            self.perf_stack.set_visible_child_name(row.perf_key)

    def _page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(16)
        box.set_margin_end(16)
        return box

    def _wrap(self, box):
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroll.set_child(box)
        return scroll

    def _big_title(self, text):
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("perf-title-big")
        return label

    def _graph_frame(self, graph):
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("graph-frame")
        frame.append(graph)
        return frame

    def _legend(self, items):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        for name, color in items:
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            swatch = Gtk.DrawingArea()
            swatch.set_content_width(12)
            swatch.set_content_height(12)
            swatch.set_valign(Gtk.Align.CENTER)
            r, g, b = color
            swatch.set_draw_func(
                lambda _a, cr, w, h, c=(r, g, b): (
                    cr.set_source_rgb(*c),
                    cr.rectangle(w / 2 - 5, h / 2 - 5, 10, 10),
                    cr.fill(),
                )
            )
            lab = Gtk.Label(label=name)
            lab.add_css_class("perf-value")
            item.append(swatch)
            item.append(lab)
            row.append(item)
        return row

    def _stat_grid(self, pairs):
        grid = Gtk.Grid(column_spacing=28, row_spacing=7)
        grid.add_css_class("stat-grid")
        values = {}
        for i, (label, init) in enumerate(pairs):
            r, c = divmod(i, 2)
            lab = Gtk.Label(label=label, xalign=0)
            lab.add_css_class("stat-label")
            lab.set_hexpand(True)
            val = Gtk.Label(label=init, xalign=0)
            val.add_css_class("stat-value")
            grid.attach(lab, c * 2, r, 1, 1)
            grid.attach(val, c * 2 + 1, r, 1, 1)
            values[label] = val
        return grid, values

    def _make_cpu_page(self):
        box = self._page()
        box.append(self._big_title("Процесор"))
        graph = make_graph(CPU_COLOR, 150)
        box.append(self._graph_frame(graph))

        cores_grid = Gtk.Grid(column_spacing=14, row_spacing=10)
        cores_grid.set_column_homogeneous(True)
        cores = []
        core_labels = []
        for i in range(CPU_COUNT):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            cell.add_css_class("core-cell")
            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            lab = Gtk.Label(label=f"Ядро {i}", xalign=0)
            lab.add_css_class("core-label")
            val = Gtk.Label(label="—", xalign=1)
            val.add_css_class("core-value")
            val.set_width_chars(4)
            val.set_hexpand(True)
            head.append(lab)
            head.append(val)
            cg = make_graph(CPU_COLOR, 40, axes=False)
            cell.append(head)
            cell.append(cg)
            cores_grid.attach(cell, i % 4, i // 4, 1, 1)
            cores.append(cg)
            core_labels.append(val)
        box.append(cores_grid)

        grid, stats = self._stat_grid(
            [
                ("Використання", "—"),
                ("Швидкість", "—"),
                ("Температура", "—"),
                ("Процеси", "—"),
                ("Потоки", "—"),
                ("Ядра", "—"),
                ("Час роботи", "—"),
                ("Дескриптори", "—"),
                ("Сокети", "—"),
            ]
        )
        box.append(grid)
        self.perf["cpu"] = {
            "graph": graph,
            "cores": cores,
            "core_labels": core_labels,
            "stats": stats,
        }
        return self._wrap(box)

    def _make_mem_page(self):
        box = self._page()
        box.append(self._big_title("Пам'ять"))
        graph = make_graph(MEM_COLOR, 150)
        box.append(self._graph_frame(graph))
        grid, stats = self._stat_grid(
            [
                ("Використання", "—"),
                ("Всього", "—"),
                ("Використано", "—"),
                ("Доступно", "—"),
                ("Кеш", "—"),
                ("Підкачка", "—"),
                ("Температура", "—"),
            ]
        )
        box.append(grid)
        self.perf["mem"] = {"graph": graph, "stats": stats}
        return self._wrap(box)

    def _make_disk_page(self):
        box = self._page()
        box.append(self._big_title("Диск"))
        graph = make_rate_graph([DISK_READ, DISK_WRITE], 150)
        box.append(self._graph_frame(graph))
        box.append(
            self._legend([("Читання", DISK_READ), ("Запис", DISK_WRITE)])
        )
        grid, stats = self._stat_grid(
            [
                ("Читання", "—"),
                ("Запис", "—"),
                ("Температура", "—"),
                ("Носій", "—"),
                ("Файлова система", "—"),
                ("Ємність", "—"),
                ("Зайнято", "—"),
                ("Вільно", "—"),
            ]
        )
        box.append(grid)
        self.perf["disk"] = {"graph": graph, "stats": stats}
        return self._wrap(box)

    def _make_net_page(self):
        box = self._page()
        box.append(self._big_title("Езернет"))
        graph = make_rate_graph([NET_DOWN, NET_UP], 150)
        box.append(self._graph_frame(graph))
        box.append(self._legend([("Отримання", NET_DOWN), ("Віддача", NET_UP)]))
        grid, stats = self._stat_grid(
            [
                ("Адаптер", "—"),
                ("Швидкість", "—"),
                ("Отримання", "—"),
                ("Віддача", "—"),
                ("Отримано (від старту системи)", "—"),
                ("Відправлено (від старту системи)", "—"),
                ("Система завантажена", "—"),
            ]
        )
        box.append(grid)
        self.perf["net"] = {"graph": graph, "stats": stats}
        return self._wrap(box)

    def _make_gpu_page(self):
        box = self._page()
        box.append(self._big_title("Графічний процесор"))
        graph = make_graph(GPU_COLOR, 150)
        box.append(self._graph_frame(graph))
        grid, stats = self._stat_grid(
            [
                ("Модель", "—"),
                ("Використання", "—"),
                ("Пам'ять", "—"),
                ("Температура", "—"),
            ]
        )
        box.append(grid)
        self.perf["gpu"] = {"graph": graph, "stats": stats}
        return self._wrap(box)

    # -------------------------------------------------------------- update
    def _tick(self):
        now = time.monotonic()
        dt = now - self._last_time or 1.0

        cpu = psutil.cpu_percent(interval=None)
        percore = psutil.cpu_percent(interval=None, percpu=True)
        vm = psutil.virtual_memory()

        # ---- CPU
        cpu_page = self.perf["cpu"]
        cpu_page["graph"].push(cpu)
        for cg, val_label, val in zip(
            cpu_page["cores"], cpu_page["core_labels"], percore
        ):
            cg.push(val)
            val_label.set_text(f"{val:.0f}%")
        freq = psutil.cpu_freq()
        cpu_page["stats"]["Використання"].set_text(f"{cpu:.0f}%")
        cpu_page["stats"]["Швидкість"].set_text(
            f"{freq.current:.0f} МГц" if freq else "—"
        )
        cpu_page["stats"]["Процеси"].set_text(str(self._proc_count))
        cpu_page["stats"]["Потоки"].set_text(str(self._thread_count))
        cpu_page["stats"]["Ядра"].set_text(
            f"{psutil.cpu_count(logical=False)} / {CPU_COUNT}"
        )
        cpu_page["stats"]["Час роботи"].set_text(fmt_uptime(time.time() - psutil.boot_time()))
        cpu_page["stats"]["Дескриптори"].set_text(str(self._fd_count()))
        cpu_page["stats"]["Сокети"].set_text("—")
        cpu_page["stats"]["Температура"].set_text(fmt_temp(cpu_temperature()))

        # ---- Пам'ять
        mem_page = self.perf["mem"]
        mem_page["graph"].push(vm.percent)
        mem_page["stats"]["Використання"].set_text(f"{vm.percent:.0f}%")
        mem_page["stats"]["Всього"].set_text(f"{vm.total / 1024**3:.1f} ГБ")
        mem_page["stats"]["Використано"].set_text(f"{vm.used / 1024**3:.1f} ГБ")
        mem_page["stats"]["Доступно"].set_text(f"{vm.available / 1024**3:.1f} ГБ")
        cached = getattr(vm, "cached", 0) + getattr(vm, "buffers", 0)
        mem_page["stats"]["Кеш"].set_text(f"{cached / 1024**3:.1f} ГБ")
        swap = psutil.swap_memory()
        mem_page["stats"]["Підкачка"].set_text(
            f"{swap.used / 1024**3:.1f} / {swap.total / 1024**3:.1f} ГБ"
        )
        mem_page["stats"]["Температура"].set_text(fmt_temp(ram_temperature()))

        # ---- Диск
        disk = psutil.disk_io_counters()
        if disk and self._last_disk:
            rd = (disk.read_bytes - self._last_disk.read_bytes) / dt / 1048576
            wr = (disk.write_bytes - self._last_disk.write_bytes) / dt / 1048576
        else:
            rd = wr = 0.0
        self._last_disk = disk
        disk_page = self.perf["disk"]
        disk_page["graph"].push([rd, wr])
        disk_page["stats"]["Читання"].set_text(f"{rd:.1f} МБ/с")
        disk_page["stats"]["Запис"].set_text(f"{wr:.1f} МБ/с")
        disk_page["stats"]["Температура"].set_text(fmt_temp(disk_temperature()))
        try:
            du = psutil.disk_usage("/")
            disk_page["stats"]["Ємність"].set_text(f"{du.total / 1024**3:.0f} ГБ")
            disk_page["stats"]["Зайнято"].set_text(
                f"{du.used / 1024**3:.0f} ГБ ({du.percent:.0f}%)"
            )
            disk_page["stats"]["Вільно"].set_text(f"{du.free / 1024**3:.0f} ГБ")
        except OSError:
            pass
        disk_page["stats"]["Носій"].set_text(
            self._root_part.device if self._root_part else "—"
        )
        disk_page["stats"]["Файлова система"].set_text(
            self._root_part.fstype if self._root_part else "—"
        )

        # ---- Езернет
        cur = psutil.net_io_counters(pernic=True).get(self.iface)
        if cur and self._last_net:
            down = (cur.bytes_recv - self._last_net.bytes_recv) / dt / 1048576
            up = (cur.bytes_sent - self._last_net.bytes_sent) / dt / 1048576
        else:
            down = up = 0.0
        self._last_net = cur
        net_page = self.perf["net"]
        net_page["graph"].push([down, up])
        net_page["stats"]["Адаптер"].set_text(self.iface or "—")
        net_page["stats"]["Отримання"].set_text(f"{down:.2f} МБ/с")
        net_page["stats"]["Віддача"].set_text(f"{up:.2f} МБ/с")
        nstat = psutil.net_if_stats().get(self.iface) if self.iface else None
        net_page["stats"]["Швидкість"].set_text(
            f"{nstat.speed} Мбіт/с" if nstat else "—"
        )
        net_page["stats"]["Отримано (від старту системи)"].set_text(
            fmt_bytes(cur.bytes_recv) if cur else "—"
        )
        net_page["stats"]["Відправлено (від старту системи)"].set_text(
            fmt_bytes(cur.bytes_sent) if cur else "—"
        )
        net_page["stats"]["Система завантажена"].set_text(
            time.strftime("%d.%m.%Y %H:%M", time.localtime(psutil.boot_time()))
        )

        # ---- Графічний процесор
        gpu_page = self.perf["gpu"]
        g = gpu_info()
        if g:
            gpu_page["graph"].push(g["util"])
            gpu_page["stats"]["Модель"].set_text(g["name"])
            gpu_page["stats"]["Використання"].set_text(f"{g['util']:.0f}%")
            gpu_page["stats"]["Пам'ять"].set_text(
                f"{g['mem_used']:.0f} / {g['mem_total']:.0f} МБ"
            )
            gpu_page["stats"]["Температура"].set_text(f"{g['temp']:.0f} °C")
        else:
            gpu_page["graph"].push(0)
            gpu_page["stats"]["Модель"].set_text("не виявлено")

        self._last_time = now

        self._refresh_rows()
        if self._start_expanded and not self._did_expand:
            self._toggle_all()
            self._did_expand = True
        return True

    def _fd_count(self):
        try:
            with open("/proc/sys/fs/file-nr", encoding="utf-8") as f:
                return int(f.read().split()[0])
        except (OSError, ValueError):
            return 0

    def _gather(self):
        rows = []
        threads = 0
        for proc in psutil.process_iter(
            ["pid", "name", "username", "memory_info", "status"]
        ):
            try:
                info = proc.info
                cpu = proc.cpu_percent(interval=None) / CPU_COUNT
                mem = (info["memory_info"].rss if info["memory_info"] else 0) / 1024**2
                threads += proc.num_threads()
                rows.append(
                    (
                        info["pid"],
                        info["name"] or "?",
                        (info["username"] or "?").split("\\")[-1],
                        round(cpu, 1),
                        round(mem, 1),
                        STATUS_UK.get(info["status"], info["status"] or "?"),
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                continue
        self._proc_count = len(rows)
        self._thread_count = threads
        return rows

    def _refresh_rows(self):
        query = self.search.get_text().lower()
        grouped = {}
        for pid, name, user, cpu, mem, status in self._gather():
            if query and query not in name.lower() and query not in str(pid):
                continue
            grouped.setdefault(name, []).append((pid, name, user, cpu, mem, status))

        seen = set()
        for name, rows in grouped.items():
            seen.update(r[0] for r in rows)
            total_cpu = round(sum(r[3] for r in rows), 1)
            total_mem = round(sum(r[4] for r in rows), 1)
            count = len(rows)

            if name in self.groups:
                git = self.groups[name]
            else:
                git = self.store.append(
                    None, [-1, name, "", 0.0, 0.0, "", True]
                )
                self.groups[name] = git
            self.store.set(
                git,
                1,
                f"{name}  ({count})",
                3,
                total_cpu,
                4,
                total_mem,
                5,
                f"{count} проц.",
            )

            for pid, _n, user, cpu, mem, status in rows:
                if pid in self.leaves and self.leaves[pid][0] == name:
                    self.store.set(
                        self.leaves[pid][1], 1, name, 2, user, 3, cpu, 4, mem, 5, status
                    )
                    continue
                if pid in self.leaves:
                    try:
                        self.store.remove(self.leaves[pid][1])
                    except (ValueError, TypeError):
                        pass
                lit = self.store.append(
                    git, [pid, name, user, cpu, mem, status, False]
                )
                self.leaves[pid] = (name, lit)

        for pid in list(self.leaves):
            if pid not in seen:
                name, lit = self.leaves.pop(pid)
                try:
                    self.store.remove(lit)
                except (ValueError, TypeError):
                    pass

        for name in list(self.groups):
            if name not in grouped:
                git = self.groups.pop(name)
                try:
                    self.store.remove(git)
                except (ValueError, TypeError):
                    pass

    # ------------------------------------------------------------- row heat
    def _make_cell_func(self, kind):
        def func(_col, renderer, model, it, _data):
            _m, sel = self.tree.get_selection().get_selected()
            selected = sel is not None and self.store.get_path(
                sel
            ) == self.store.get_path(it)
            if selected:
                renderer.set_property("cell-background-rgba", _TRANSPARENT)
            elif kind == "mem":
                renderer.set_property(
                    "cell-background-rgba", heat(model.get_value(it, 4) / self.total_mb)
                )
            else:
                renderer.set_property(
                    "cell-background-rgba", heat(model.get_value(it, 3) / 100.0)
                )

            if kind == "cpu":
                renderer.set_property("text", f"{model.get_value(it, 3):.1f}")
            elif kind == "mem":
                mb = model.get_value(it, 4)
                if mb < 300:
                    renderer.set_property("text", f"{mb:.0f} МБ")
                else:
                    renderer.set_property("text", f"{mb / 1024:.2f} ГБ")
            elif kind == "pid":
                if model.get_value(it, 6):
                    renderer.set_property("text", "")
                else:
                    renderer.set_property("text", str(model.get_value(it, 0)))

        return func

    # --------------------------------------------------------------- kill
    def _build_actions(self):
        act = Gio.SimpleAction.new("kill", None)
        act.connect("activate", lambda *_: self._kill_selected())
        self.add_action(act)

    def set_active_tab(self, name):
        if not name:
            return
        component = None
        if ":" in name:
            name, component = name.split(":", 1)
        self.stack.set_visible_child_name(name)
        if component:
            self.perf_stack.set_visible_child_name(component)
            row = self.perf_rows.get(component)
            if row is not None:
                self.perf_list.select_row(row)

    def _selection(self):
        _model, it = self.tree.get_selection().get_selected()
        if it is None:
            return None
        return (
            it,
            self.store.get_value(it, 6),
            self.store.get_value(it, 0),
            self.store.get_value(it, 1),
        )

    def _toggle_all(self):
        if self.all_expanded:
            for it in self.groups.values():
                self.tree.collapse_row(self.store.get_path(it))
            self.toggle_btn.set_label("Розгорнути все")
            self.all_expanded = False
        else:
            for it in self.groups.values():
                self.tree.expand_row(self.store.get_path(it), False)
            self.toggle_btn.set_label("Згорнути все")
            self.all_expanded = True

    def _on_row_activated(self, tree, path, _col):
        if tree.row_expanded(path):
            tree.collapse_row(path)
        else:
            tree.expand_row(path, False)

    def _kill_selected(self):
        sel = self._selection()
        if sel is None:
            return None
        it, is_group, pid, name = sel

        if is_group:
            group = name.split("  (")[0]
            pids = []
            child = self.store.iter_children(it)
            while child is not None:
                pids.append(self.store.get_value(child, 0))
                child = self.store.iter_next(child)
            if not pids:
                return None
            heading = "Завершити групу?"
            body = f"Усі процеси «{group}» ({len(pids)}) буде зупинено (SIGTERM)."
        else:
            try:
                proc = psutil.Process(pid)
            except psutil.NoSuchProcess:
                return None
            pids = [pid]
            heading = "Завершити завдання?"
            body = f"«{proc.name()}» (PID {pid}) буде надіслано SIGTERM."

        dialog = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dialog.add_response("cancel", "Скасувати")
        dialog.add_response("kill", "Завершити")
        dialog.set_response_appearance("kill", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_kill_response, pids)
        dialog.present()
        return None

    def _on_kill_response(self, _dialog, response, pids):
        if response != "kill":
            return
        denied = []
        for pid in pids:
            try:
                psutil.Process(pid).terminate()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                denied.append(pid)
        if denied:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Недостатньо прав",
                body=f"Потрібні права root для: {', '.join(map(str, denied))}.",
            )
            dialog.add_response("ok", "Гаразд")
            dialog.present()

    def _on_right_click(self, _gesture, _n, x, y):
        path = self.tree.get_path_at_pos(int(x), int(y))
        if path is None:
            return
        self.tree.get_selection().select_path(path)
        if self._popover is None:
            self._popover = Gtk.PopoverMenu()
            self._popover.set_parent(self.tree)
            menu = Gio.Menu()
            menu.append("Завершити завдання", "win.kill")
            self._popover.set_menu_model(menu)
        rect = Gdk.Rectangle()
        rect.x, rect.y = int(x), int(y)
        self._popover.set_pointing_to(rect)
        self._popover.popup()

    # ----------------------------------------------------------- snapshot
    def _save_snapshot(self):
        import os

        path = os.path.expanduser(
            f"~/Pictures/system-monitor-{time.strftime('%Y%m%d-%H%M%S')}.png"
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            save_png(self, path)
            self.overlay.add_toast(Adw.Toast.new(f"Збережено: {path}"))
        except Exception as exc:  # noqa: BLE001
            self.overlay.add_toast(Adw.Toast.new(f"Помилка знімка: {exc}"))


class App(Adw.Application):
    def __init__(self, snapshot_path=None, tab=None, expand=False):
        super().__init__(
            application_id="org.example.SystemMonitor",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.snapshot_path = snapshot_path
        self.tab = tab
        self.expand = expand

    def do_activate(self):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        provider = Gtk.CssProvider()
        provider.load_from_string(WIN_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        win = MonitorWindow(application=self, expand=self.expand)
        win.set_active_tab(self.tab)
        win.present()
        if self.snapshot_path:
            GLib.timeout_add(1200, self._snap, win)

    def _snap(self, win):
        self._snap_tries = getattr(self, "_snap_tries", 0) + 1
        try:
            save_png(win, self.snapshot_path)
        except Exception as exc:  # noqa: BLE001
            if self._snap_tries < 30:
                GLib.timeout_add(300, self._snap, win)
                return False
            print(f"snapshot error: {exc}", file=sys.stderr)
            self.quit()
            return False
        print(f"snapshot: {self.snapshot_path}")
        self.quit()
        return False


def _arg(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


if __name__ == "__main__":
    if not CAIRO_OK:
        print("Примітка: немає python3-gi-cairo — графіки в резервному режимі.", file=sys.stderr)
    App(
        snapshot_path=_arg("--snapshot"),
        tab=_arg("--tab"),
        expand="--expand" in sys.argv,
    ).run(sys.argv[:1])
