import asyncio
import csv
import io
import math
import os
import time
import urllib.parse
from datetime import datetime

import flet as ft
import flet_charts as ftc
import flet_geolocator as ftg

from models import (
    DEFAULT_PARAMS,
    LocationPoint,
    SQLiteStore,
    Settings,
    TripTracker,
    format_duration,
    timestamp_ms,
)

# Colors matching iappyxOS theme
COLOR_BG = "#0d0d1a"
COLOR_CARD = "#1a1a2e"
COLOR_HERO = "#0f3460"
COLOR_CYAN = "#4FC3F7"
COLOR_DANGER = "#FF6B6B"
COLOR_TEXT_PRIMARY = "#eaeaea"
COLOR_TEXT_MUTED = "rgba(255, 255, 255, 0.5)"

SAQ_COLORS = {
    "A": "#69F0AE",
    "B": "#81C784",
    "C": "#FFF176",
    "D": "#FFB74D",
    "E": "#FF8A65",
    "F": "#FF6B6B",
    "-": "#718096",
}


class RinApp:
    """Flet UI matching iappyxOS RIN08-Live dark theme aesthetics."""

    def __init__(self, page: ft.Page):
        self._page = page
        self._page.bgcolor = COLOR_BG
        self._page.padding = 0
        self._page.spacing = 0
        self.store = SQLiteStore()
        self.settings = self.store.load_settings()
        self.tracker = TripTracker(self.settings)
        self.recording = False
        self.active_tab = "dashboard"
        self.csv_visible = False
        self.last_position = None
        self.logs = ["RIN08 Live bereit.", "SQLite-Speicher initialisiert."]

        # GPS background service
        self.geolocator = ftg.Geolocator(
            configuration=ftg.GeolocatorAndroidConfiguration(
                accuracy=ftg.GeolocatorPositionAccuracy.BEST_FOR_NAVIGATION,
                distance_filter=1,
                interval_duration=int(self.settings.gps_interval * 1000),
                foreground_notification_config=ftg.ForegroundNotificationConfiguration(
                    notification_title="RIN08-Live",
                    notification_text="RIN08-Live zeichnet deine Route auf.",
                ),
            ),
            on_position_change=self._on_position_change,
            on_error=self._on_location_error,
        )
        self._page.services.append(self.geolocator)

        # Root layout
        self.root = ft.Column(
            expand=True,
            spacing=0,
            controls=[],
        )
        self._page.add(self.root)
        self.render()

        # Start timer clock
        if hasattr(self._page, "run_task"):
            self._page.run_task(self._clock)
        else:
            try:
                asyncio.create_task(self._clock())
            except RuntimeError:
                pass

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logs.insert(0, f"[{stamp}] {message}")
        self.logs = self.logs[:80]

    async def _clock(self) -> None:
        while True:
            await asyncio.sleep(1)
            if self.recording and self.active_tab == "dashboard":
                self.render()

    def _on_location_error(self, event) -> None:
        err_msg = getattr(event, "data", str(event))
        self._log(f"GPS-Fehler: {err_msg}")
        if self.active_tab == "debug":
            self.render()

    def _on_position_change(self, event: ftg.GeolocatorPositionChangeEvent) -> None:
        if event.position is not None:
            self._process_position(event.position)

    def _process_position(self, position: ftg.GeolocatorPosition) -> None:
        self.last_position = position
        if not self.recording:
            if self.active_tab == "debug":
                self.render()
            return

        point, note = self.tracker.ingest(
            latitude=position.latitude,
            longitude=position.longitude,
            accuracy=position.accuracy or 0.0,
            timestamp=timestamp_ms(position.timestamp),
        )
        if note:
            self._log(note)
        if point:
            self.store.add_point(point)
            self._log(
                f"GPS Tick: {point.total_distance_km:.2f} km, V={point.instant_speed_kmh:.1f} km/h, SAQ {point.saq}"
            )
        if self.active_tab in {"dashboard", "data", "debug"}:
            self.render()

    async def toggle_recording(self, _event) -> None:
        if self.recording:
            self.recording = False
            self._log("Aufzeichnung beendet.")
            self.render()
            return

        try:
            permission = await self.geolocator.request_permission()
            if permission not in {
                ftg.GeolocatorPermissionStatus.WHILE_IN_USE,
                ftg.GeolocatorPermissionStatus.ALWAYS,
            }:
                self._log("Standortberechtigung wurde nicht erteilt.")
                self.render()
                return
        except Exception as perm_err:
            self._log(f"Berechtigungs-Check: {perm_err}")

        self.tracker.reset()
        self.tracker.start(int(time.time() * 1000))
        self.store.start_session(self.tracker.session_id, self.settings.mode)
        self.recording = True
        self._log("Aufzeichnung gestartet (2s Intervall, 5m Genauigkeit).")
        try:
            position = await self.geolocator.get_current_position()
            if position is not None:
                self._process_position(position)
        except Exception as error:
            self._log(f"Erste GPS-Abfrage: {error}")
        self.render()

    async def fetch_location(self, _event) -> None:
        try:
            position = await self.geolocator.get_current_position()
            if position is None:
                self._log("GPS lieferte keine Position.")
            else:
                self._log("Manuelle GPS-Abfrage erfolgreich.")
                self._process_position(position)
        except Exception as error:
            self._log(f"GPS-Abfrage fehlgeschlagen: {error}")
        self.render()

    def switch_tab(self, tab: str):
        def handler(_event) -> None:
            self.active_tab = tab
            self.render()

        return handler

    def reset_session(self, _event) -> None:
        if self.tracker.session_id:
            self.store.clear_session(self.tracker.session_id)
        self.tracker.reset()
        self.recording = False
        self.csv_visible = False
        self._log("Aufzeichnung und Messdaten zurückgesetzt.")
        self.render()

    def toggle_csv(self, _event) -> None:
        self.csv_visible = not self.csv_visible
        self.render()

    def download_csv(self, _event) -> None:
        points = self.store.points_for(self.tracker.session_id)
        csv_text = self._csv_for(points)
        encoded = urllib.parse.quote(csv_text)
        data_uri = f"data:text/csv;charset=utf-8,{encoded}"
        
        try:
            os.makedirs("exports", exist_ok=True)
            filename = f"exports/rin08_{self.tracker.session_id or 'session'}.csv"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(csv_text)
            self._log(f"CSV exportiert nach {filename}")
        except Exception as err:
            self._log(f"Lokales CSV Speichern: {err}")

        self._page.launch_url(data_uri)

    def copy_csv(self, _event) -> None:
        points = self.store.points_for(self.tracker.session_id)
        csv_text = self._csv_for(points)
        self._page.set_clipboard(csv_text)
        self._log("CSV in Zwischenablage kopiert.")

    def save_settings(self, _event) -> None:
        self.store.save_settings(self.settings)
        self._log("Konfiguration gespeichert.")
        self.render()

    def reset_parameters(self, _event) -> None:
        self.settings.params = {
            mode: {key: list(values) for key, values in curve.items()}
            for mode, curve in DEFAULT_PARAMS.items()
        }
        self.store.save_settings(self.settings)
        self._log("SAQ-Parameter auf RIN-Standardwerte zurückgesetzt.")
        self.render()

    def set_number(self, field_name: str):
        def handler(event) -> None:
            try:
                val = float(event.control.value.replace(",", "."))
                setattr(self.settings, field_name, val)
            except (TypeError, ValueError):
                return

        return handler

    def set_mode(self, event) -> None:
        self.settings.mode = event.control.value
        self.render()

    def set_parameter(self, key: str, index: int):
        def handler(event) -> None:
            try:
                val = float(event.control.value.replace(",", "."))
                self.settings.params[self.settings.mode][key][index] = val
            except (TypeError, ValueError):
                return

        return handler

    # UI Components
    def _metric_card(self, title: str, value: str, unit: str = "", highlight: bool = False) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(title, size=10, weight=ft.FontWeight.BOLD, color="rgba(255,255,255,0.5)"),
                    ft.Row(
                        controls=[
                            ft.Text(value, size=24, weight=ft.FontWeight.BOLD, color=COLOR_CYAN if highlight else COLOR_TEXT_PRIMARY),
                            ft.Text(unit, size=11, color="rgba(255,255,255,0.4)") if unit else ft.Container(),
                        ],
                        alignment=ft.MainAxisAlignment.START,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                        spacing=4,
                    ),
                ],
                spacing=4,
            ),
            bgcolor=COLOR_CARD,
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            expand=True,
            border=ft.Border.all(1, "rgba(255,255,255,0.03)"),
        )

    def _tab_button(self, label: str, tab: str) -> ft.Control:
        is_active = self.active_tab == tab
        return ft.Button(
            content=ft.Text(
                label,
                size=12,
                weight=ft.FontWeight.BOLD if is_active else ft.FontWeight.NORMAL,
                color="#0d0d1a" if is_active else "rgba(255,255,255,0.7)",
            ),
            bgcolor=COLOR_CYAN if is_active else "rgba(255,255,255,0.06)",
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.Padding.symmetric(vertical=8, horizontal=8),
                elevation=0,
            ),
            on_click=self.switch_tab(tab),
            expand=True,
        )

    def dashboard_view(self) -> ft.Column:
        metrics = self.tracker.metrics
        saq = self.tracker.current_saq()
        letter = saq.letter if self.recording else "-"
        grade_desc = f"Stufe {saq.grade}" if self.recording else "Stufe -"
        saq_color = SAQ_COLORS.get(letter, "#718096")

        elapsed = (
            int(time.time() * 1000) - metrics.start_time_ms
            if self.recording and metrics.start_time_ms
            else 0
        )

        # 1. Hero Card: ANGEBOTSQUALITÄT (SAQ)
        saq_hero_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "ANGEBOTSQUALITÄT (SAQ)",
                        size=11,
                        weight=ft.FontWeight.BOLD,
                        color="rgba(255, 255, 255, 0.6)",
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        letter,
                        size=52,
                        weight=ft.FontWeight.W_800,
                        color=saq_color,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        grade_desc,
                        size=13,
                        color="rgba(255, 255, 255, 0.7)",
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=2,
            ),
            bgcolor=COLOR_HERO,
            border=ft.Border.all(1, "rgba(79, 195, 247, 0.4)"),
            border_radius=12,
            padding=ft.Padding.symmetric(vertical=14, horizontal=16),
            alignment=ft.Alignment.CENTER,
        )

        # 2. Line Chart (right-aligned SAQ labels, no top legend)
        chart_container = ft.Container(
            content=self._chart(),
            height=260,
            bgcolor=COLOR_CARD,
            border_radius=12,
            padding=ft.Padding.only(top=14, bottom=8, left=10, right=14),
            border=ft.Border.all(1, "rgba(255, 255, 255, 0.04)"),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

        # 3. Two-Column Metric Cards
        row_dist = ft.Row(
            controls=[
                self._metric_card("FAHRTDISTANZ", f"{metrics.total_distance_km:.2f}", "km", highlight=True),
                self._metric_card("LUFTLINIE", f"{metrics.straight_distance_km:.2f}", "km", highlight=True),
            ],
            spacing=10,
        )

        row_speed = ft.Row(
            controls=[
                self._metric_card("V-AKTUELL (5 PKT)", f"{metrics.current_speed_kmh:.1f}", "km/h"),
                self._metric_card("V-LUFTLINIE", f"{metrics.straight_speed_kmh:.1f}", "km/h"),
            ],
            spacing=10,
        )

        row_time = ft.Row(
            controls=[
                self._metric_card("ZEIT (TOTAL)", format_duration(elapsed)),
                self._metric_card("ZEIT (BEWEGUNG)", format_duration(metrics.moving_time_ms)),
            ],
            spacing=10,
        )

        return ft.Column(
            scroll=ft.ScrollMode.AUTO,
            spacing=12,
            controls=[
                saq_hero_card,
                chart_container,
                row_dist,
                row_speed,
                row_time,
                ft.Container(height=8),
            ],
        )

    def _chart(self) -> ftc.LineChart:
        metrics = self.tracker.metrics
        curr_dist = metrics.straight_distance_km
        curr_speed = metrics.straight_speed_kmh

        # Bounds and offsets with settings
        step_x = max(1.0, float(getattr(self.settings, "chart_x_step", 10.0)))
        min_distance = round(float(getattr(self.settings, "chart_min_x", 0.0)), 2)
        default_max_x = float(getattr(self.settings, "chart_default_max_x", 50.0))
        x_offset = float(getattr(self.settings, "chart_x_offset", 10.0))
        max_dist_target = max(default_max_x, curr_dist + x_offset)
        max_distance = round(math.ceil(max_dist_target / step_x) * step_x, 2)

        step_y = max(1.0, float(getattr(self.settings, "chart_y_step", 10.0)))
        min_speed = round(max(0.0, float(getattr(self.settings, "chart_min_y", 0.0))), 2)
        default_max_y = float(getattr(self.settings, "chart_default_max_y", 60.0))
        y_offset = float(getattr(self.settings, "chart_y_offset", 10.0))
        min_allowed_y = max(20.0, default_max_y)
        max_speed_target = max(min_allowed_y, curr_speed + y_offset)
        max_speed = round(math.ceil(max_speed_target / step_y) * step_y, 2)

        curve_colors = [
            "#69F0AE",  # SAQ A
            "#81C784",  # SAQ B
            "#FFF176",  # SAQ C
            "#FFB74D",  # SAQ D
            "#FF8A65",  # SAQ E
        ]

        series = []
        params = self.settings.params[self.settings.mode]
        right_labels = []

        # Generate SAQ curves with rounded coordinates (2 digits after decimal)
        for index in range(5):
            points = []
            for step in range(51):
                distance = min_distance + (max_distance - min_distance) * (step / 50.0)
                safe_dist = max(0.05, distance)
                denom = (params["a"][index] * (safe_dist ** params["b"][index])) + params["c"][index]
                speed = (1.0 / denom) if denom else 0.0
                points.append(
                    ftc.LineChartDataPoint(
                        round(distance, 2),
                        round(min(speed, max_speed), 2),
                    )
                )

            # Top right of graph datapoint labeled on the right side
            end_denom = (params["a"][index] * (max_distance ** params["b"][index])) + params["c"][index]
            end_speed = round(min((1.0 / end_denom) if end_denom else 0.0, max_speed), 2)
            saq_letter = ["A", "B", "C", "D", "E"][index]
            right_labels.append(
                ftc.ChartAxisLabel(
                    value=end_speed,
                    label=ft.Text(
                        f"SAQ {saq_letter}",
                        size=9,
                        weight=ft.FontWeight.BOLD,
                        color=curve_colors[index],
                    ),
                )
            )

            series.append(
                ftc.LineChartData(
                    points=points,
                    color=curve_colors[index],
                    stroke_width=1.5,
                    curved=True,
                )
            )

        # Must sort right_labels strictly ascending by value to satisfy fl_chart
        right_labels.sort(key=lambda l: l.value)

        # GPS trajectory with rounded coordinates
        points_history = self.store.points_for(self.tracker.session_id)
        if points_history:
            trajectory = [
                ftc.LineChartDataPoint(
                    round(pt.straight_distance_km, 2),
                    round(pt.straight_speed_kmh, 2),
                )
                for pt in points_history
            ]
            series.append(
                ftc.LineChartData(
                    points=trajectory,
                    color=COLOR_CYAN,
                    stroke_width=3,
                    curved=False,
                    point=ftc.ChartCirclePoint(radius=3, color=COLOR_CYAN),
                )
            )

        # Explicit clean labels for bottom axis (x) avoiding overlapping integers
        bottom_labels = []
        x_val = min_distance
        while x_val <= max_distance + 0.001:
            lbl_str = f"{int(x_val)}" if x_val == int(x_val) else f"{x_val:.1f}"
            bottom_labels.append(
                ftc.ChartAxisLabel(
                    value=round(x_val, 2),
                    label=ft.Text(lbl_str, size=9, color="rgba(255,255,255,0.6)"),
                )
            )
            x_val += step_x

        # Explicit clean labels for left axis (y) avoiding overlapping integers
        left_labels = []
        y_val = min_speed
        while y_val <= max_speed + 0.001:
            lbl_str = f"{int(y_val)}" if y_val == int(y_val) else f"{y_val:.1f}"
            left_labels.append(
                ftc.ChartAxisLabel(
                    value=round(y_val, 2),
                    label=ft.Text(lbl_str, size=9, color="rgba(255,255,255,0.6)"),
                )
            )
            y_val += step_y

        return ftc.LineChart(
            data_series=series,
            min_x=min_distance,
            max_x=max_distance,
            min_y=min_speed,
            max_y=max_speed,
            interactive=True,
            left_axis=ftc.ChartAxis(
                labels=left_labels,
                title=ft.Text("V-Luftlinie (km/h)", size=10, color="rgba(255,255,255,0.6)"),
                title_size=24,
                show_labels=True,
                label_size=28,
            ),
            bottom_axis=ftc.ChartAxis(
                labels=bottom_labels,
                title=ft.Text("Luftliniendistanz (km)", size=10, color="rgba(255,255,255,0.6)"),
                title_size=20,
                show_labels=True,
                label_size=20,
            ),
            right_axis=ftc.ChartAxis(
                labels=right_labels,
                show_labels=True,
                label_size=55,
            ),
            horizontal_grid_lines=ftc.ChartGridLines(color="rgba(255,255,255,0.06)", width=1),
            vertical_grid_lines=ftc.ChartGridLines(color="rgba(255,255,255,0.06)", width=1),
        )

    def data_view(self) -> ft.Column:
        points = self.store.points_for(self.tracker.session_id)
        
        # Summary & Export Action Bar
        action_row = ft.Row(
            controls=[
                ft.Button(
                    content=ft.Row([ft.Icon(ft.Icons.DOWNLOAD, size=16), ft.Text("CSV Export")]),
                    bgcolor=COLOR_CYAN,
                    color="#0d0d1a",
                    on_click=self.download_csv,
                    expand=True,
                ),
                ft.Button(
                    content=ft.Row([ft.Icon(ft.Icons.CONTENT_COPY, size=16), ft.Text("Kopieren")]),
                    bgcolor="rgba(255,255,255,0.1)",
                    color=COLOR_TEXT_PRIMARY,
                    on_click=self.copy_csv,
                    expand=True,
                ),
                ft.Button(
                    content=ft.Row([ft.Icon(ft.Icons.VISIBILITY, size=16), ft.Text("Vorschau")]),
                    bgcolor="rgba(255,255,255,0.1)",
                    color=COLOR_TEXT_PRIMARY,
                    on_click=self.toggle_csv,
                    expand=True,
                ),
            ],
            spacing=8,
        )

        rows = []
        if not points:
            rows.append(
                ft.Container(
                    content=ft.Text("Noch keine Datenpunkte aufgezeichnet.", color="rgba(255,255,255,0.4)"),
                    alignment=ft.Alignment.CENTER,
                    padding=20,
                )
            )
        else:
            rows.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Text("Zeit", size=11, weight=ft.FontWeight.BOLD, color="rgba(255,255,255,0.6)", expand=2),
                            ft.Text("V-Akt", size=11, weight=ft.FontWeight.BOLD, color="rgba(255,255,255,0.6)", expand=2),
                            ft.Text("V-Luftlinie", size=11, weight=ft.FontWeight.BOLD, color="rgba(255,255,255,0.6)", expand=2),
                            ft.Text("Luftlinie", size=11, weight=ft.FontWeight.BOLD, color="rgba(255,255,255,0.6)", expand=2),
                            ft.Text("SAQ", size=11, weight=ft.FontWeight.BOLD, color="rgba(255,255,255,0.6)", expand=1),
                        ]
                    ),
                    bgcolor="rgba(0,0,0,0.3)",
                    padding=ft.Padding.symmetric(vertical=8, horizontal=10),
                    border_radius=6,
                )
            )
            for pt in reversed(points[-100:]):
                time_str = datetime.fromtimestamp(pt.timestamp_ms / 1000).strftime("%H:%M:%S")
                saq_col = SAQ_COLORS.get(pt.saq, "#ccc")
                rows.append(
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Text(time_str, size=11, color="rgba(255,255,255,0.8)", expand=2),
                                ft.Text(f"{pt.instant_speed_kmh:.1f}", size=11, color="rgba(255,255,255,0.8)", expand=2),
                                ft.Text(f"{pt.straight_speed_kmh:.1f}", size=11, color="rgba(255,255,255,0.8)", expand=2),
                                ft.Text(f"{pt.straight_distance_km:.2f}", size=11, color="rgba(255,255,255,0.8)", expand=2),
                                ft.Container(
                                    content=ft.Text(pt.saq, size=11, weight=ft.FontWeight.BOLD, color="#0d0d1a"),
                                    bgcolor=saq_col,
                                    border_radius=4,
                                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                    alignment=ft.Alignment.CENTER,
                                    width=24,
                                ),
                            ]
                        ),
                        padding=ft.Padding.symmetric(vertical=6, horizontal=10),
                        border=ft.Border(bottom=ft.BorderSide(1, "rgba(255,255,255,0.04)")),
                    )
                )

        table_box = ft.Container(
            content=ft.Column(controls=rows, scroll=ft.ScrollMode.AUTO, spacing=2),
            bgcolor=COLOR_CARD,
            border_radius=12,
            padding=8,
            expand=True,
        )

        controls = [action_row]

        if self.csv_visible:
            controls.append(
                ft.TextField(
                    value=self._csv_for(points),
                    multiline=True,
                    min_lines=6,
                    max_lines=10,
                    read_only=True,
                    bgcolor="#0d0d1a",
                    color="#69F0AE",
                    text_size=11,
                )
            )

        controls.append(table_box)
        controls.append(
            ft.Button(
                content=ft.Text("Aufzeichnung zurücksetzen"),
                bgcolor="rgba(255, 107, 107, 0.15)",
                color=COLOR_DANGER,
                on_click=self.reset_session,
            )
        )

        return ft.Column(controls=controls, expand=True, spacing=10)

    def _csv_for(self, points: list[LocationPoint]) -> str:
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow([
            "SessionID", "Timestamp_ms", "ISO_Time", "Latitude", "Longitude",
            "Accuracy_m", "V_Aktuell_kmh", "V_Luftlinie_kmh", "Luftlinie_km",
            "Fahrtdistanz_km", "SAQ"
        ])
        for pt in points:
            iso_time = datetime.fromtimestamp(pt.timestamp_ms / 1000).isoformat()
            writer.writerow([
                self.tracker.session_id or "default",
                pt.timestamp_ms,
                iso_time,
                f"{pt.latitude:.6f}",
                f"{pt.longitude:.6f}",
                f"{pt.accuracy_m:.1f}",
                f"{pt.instant_speed_kmh:.1f}",
                f"{pt.straight_speed_kmh:.1f}",
                f"{pt.straight_distance_km:.3f}",
                f"{pt.total_distance_km:.3f}",
                pt.saq,
            ])
        return output.getvalue()

    def debug_view(self) -> ft.Column:
        pos = self.last_position
        if pos:
            loc_info = f"Lat: {pos.latitude:.6f} · Lon: {pos.longitude:.6f} · Genauigkeit: {getattr(pos, 'accuracy', 0.0) or 0.0:.1f} m"
        else:
            loc_info = "Warte auf GPS-Signal..."

        return ft.Column(
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Row([ft.Text("Status:", weight=ft.FontWeight.BOLD), ft.Text("RECORDING" if self.recording else "INAKTIV", color=COLOR_DANGER if self.recording else COLOR_CYAN)]),
                            ft.Row([ft.Text("GPS Signal:", weight=ft.FontWeight.BOLD), ft.Text(loc_info, size=12)]),
                            ft.Row([ft.Text("Intervall / Genauigkeit:", weight=ft.FontWeight.BOLD), ft.Text(f"{self.settings.gps_interval} s / < {self.settings.min_accuracy} m", size=12)]),
                        ],
                        spacing=6,
                    ),
                    bgcolor=COLOR_CARD,
                    border_radius=10,
                    padding=14,
                ),
                ft.Button(
                    content=ft.Text("GPS manuell abfragen"),
                    bgcolor=COLOR_CYAN,
                    color="#0d0d1a",
                    on_click=self.fetch_location,
                ),
                ft.Text("Live Console Log", size=13, weight=ft.FontWeight.BOLD),
                ft.Container(
                    content=ft.Text("\n".join(self.logs), size=11, color="#69F0AE", selectable=True),
                    bgcolor="rgba(0,0,0,0.5)",
                    border_radius=8,
                    padding=12,
                    border=ft.Border.all(1, "rgba(255,255,255,0.08)"),
                    expand=True,
                ),
            ],
            expand=True,
            spacing=10,
        )

    def settings_view(self) -> ft.Column:
        filter_group = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Filter & Evaluierung", size=14, weight=ft.FontWeight.BOLD, color=COLOR_CYAN),
                    ft.Row(
                        controls=[
                            ft.Text("Bewertungsmodus", size=13, expand=True),
                            ft.Dropdown(
                                value=self.settings.mode,
                                width=120,
                                options=[
                                    ft.DropdownOption(key="PKW", text="PKW"),
                                    ft.DropdownOption(key="OEV", text="ÖV"),
                                    ft.DropdownOption(key="IOE", text="IÖ"),
                                ],
                                on_select=self.set_mode,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            ft.Column([
                                ft.Text("Max V-Aktuell (km/h)", size=13),
                                ft.Text("Spikes ignorieren", size=10, color=COLOR_TEXT_MUTED),
                            ], spacing=0, expand=True),
                            ft.TextField(value=str(int(self.settings.max_current_speed)), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("max_current_speed")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            ft.Text("Max V-Luft (km/h)", size=13, expand=True),
                            ft.TextField(value=str(int(self.settings.max_straight_speed)), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("max_straight_speed")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            ft.Text("minimale Bewegungsgeschwindigkeit (km/h)", size=13, expand=True),
                            ft.TextField(value=str(int(self.settings.moving_cutoff)), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("moving_cutoff")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            ft.Text("Min. GPS-Genauigkeit (m)", size=13, expand=True),
                            ft.TextField(value=str(int(self.settings.min_accuracy)), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("min_accuracy")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            ft.Text("GPS-Intervall (Sekunden)", size=13, expand=True),
                            ft.TextField(value=str(self.settings.gps_interval), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("gps_interval")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                ],
                spacing=10,
            ),
            bgcolor=COLOR_CARD,
            border_radius=12,
            padding=14,
        )

        param_rows = [
            ft.Row(
                controls=[
                    ft.Text("SAQ", size=12, weight=ft.FontWeight.BOLD, color="rgba(255,255,255,0.7)", width=35),
                    ft.Text("a", size=12, weight=ft.FontWeight.BOLD, color="rgba(255,255,255,0.7)", expand=True, text_align=ft.TextAlign.CENTER),
                    ft.Text("b", size=12, weight=ft.FontWeight.BOLD, color="rgba(255,255,255,0.7)", expand=True, text_align=ft.TextAlign.CENTER),
                    ft.Text("c", size=12, weight=ft.FontWeight.BOLD, color="rgba(255,255,255,0.7)", expand=True, text_align=ft.TextAlign.CENTER),
                ]
            )
        ]

        current_params = self.settings.params[self.settings.mode]
        for idx, letter in enumerate(["A", "B", "C", "D", "E"]):
            param_rows.append(
                ft.Row(
                    controls=[
                        ft.Text(letter, size=13, weight=ft.FontWeight.BOLD, color=SAQ_COLORS[letter], width=35),
                        ft.TextField(value=str(current_params["a"][idx]), text_align=ft.TextAlign.CENTER, expand=True, on_change=self.set_parameter("a", idx)),
                        ft.TextField(value=str(current_params["b"][idx]), text_align=ft.TextAlign.CENTER, expand=True, on_change=self.set_parameter("b", idx)),
                        ft.TextField(value=str(current_params["c"][idx]), text_align=ft.TextAlign.CENTER, expand=True, on_change=self.set_parameter("c", idx)),
                    ],
                    spacing=6,
                )
            )

        saq_group = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("SAQ Parameter (Kurven)", size=14, weight=ft.FontWeight.BOLD, color=COLOR_CYAN),
                    *param_rows,
                    ft.Container(height=6),
                    ft.Row(
                        controls=[
                            ft.Button(
                                content=ft.Text("Reset Default"),
                                bgcolor="rgba(255, 107, 107, 0.15)",
                                color=COLOR_DANGER,
                                on_click=self.reset_parameters,
                                expand=True,
                            ),
                            ft.Button(
                                content=ft.Text("Speichern"),
                                bgcolor=COLOR_CYAN,
                                color="#0d0d1a",
                                on_click=self.save_settings,
                                expand=True,
                            ),
                        ],
                        spacing=10,
                    ),
                ],
                spacing=8,
            ),
            bgcolor=COLOR_CARD,
            border_radius=12,
            padding=14,
        )

        chart_group = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Diagramm Einstellungen (Bounds & Offset)", size=14, weight=ft.FontWeight.BOLD, color=COLOR_CYAN),
                    ft.Row(
                        controls=[
                            ft.Text("Standard X-Max (Luftlinie km)", size=13, expand=True),
                            ft.TextField(value=str(int(getattr(self.settings, "chart_default_max_x", 50.0))), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("chart_default_max_x")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            ft.Column([
                                ft.Text("Standard Y-Max (V-Luftlinie km/h)", size=13),
                                ft.Text("Mindestens 20 km/h", size=10, color=COLOR_TEXT_MUTED),
                            ], spacing=0, expand=True),
                            ft.TextField(value=str(int(getattr(self.settings, "chart_default_max_y", 60.0))), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("chart_default_max_y")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            ft.Text("X-Offset (km)", size=13, expand=True),
                            ft.TextField(value=str(int(getattr(self.settings, "chart_x_offset", 10.0))), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("chart_x_offset")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            ft.Text("Y-Offset (km/h)", size=13, expand=True),
                            ft.TextField(value=str(int(getattr(self.settings, "chart_y_offset", 10.0))), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("chart_y_offset")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            ft.Text("X-Schrittweite (Raster km)", size=13, expand=True),
                            ft.TextField(value=str(int(getattr(self.settings, "chart_x_step", 10.0))), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("chart_x_step")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            ft.Text("Y-Schrittweite (Raster km/h)", size=13, expand=True),
                            ft.TextField(value=str(int(getattr(self.settings, "chart_y_step", 10.0))), width=120, text_align=ft.TextAlign.RIGHT, on_change=self.set_number("chart_y_step")),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                ],
                spacing=10,
            ),
            bgcolor=COLOR_CARD,
            border_radius=12,
            padding=14,
        )

        return ft.Column(
            scroll=ft.ScrollMode.AUTO,
            controls=[
                filter_group,
                chart_group,
                saq_group,
                ft.Container(height=10),
            ],
            spacing=12,
        )

    def render(self) -> None:
        views = {
            "dashboard": self.dashboard_view,
            "data": self.data_view,
            "debug": self.debug_view,
            "settings": self.settings_view,
        }
        content = views[self.active_tab]()

        header_bar = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("RIN08-Live", size=20, weight=ft.FontWeight.BOLD, color=COLOR_CYAN),
                    ft.Row(
                        controls=[
                            self._tab_button("Dashboard", "dashboard"),
                            self._tab_button("Daten", "data"),
                            self._tab_button("Debug", "debug"),
                            self._tab_button("⚙ Config", "settings"),
                        ],
                        spacing=6,
                    ),
                ],
                spacing=10,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            bgcolor=COLOR_BG,
            border=ft.Border(bottom=ft.BorderSide(1, "rgba(255,255,255,0.06)")),
        )

        action_button = ft.Button(
            content=ft.Text(
                "AUFZEICHNUNG BEENDEN" if self.recording else "AUFZEICHNUNG STARTEN",
                size=16,
                weight=ft.FontWeight.W_800,
                color="#ffffff" if self.recording else "#0d0d1a",
            ),
            bgcolor=COLOR_DANGER if self.recording else COLOR_CYAN,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=50),
                padding=ft.Padding.symmetric(vertical=16),
            ),
            on_click=self.toggle_recording,
            width=360,
        )

        bottom_bar = ft.Container(
            content=action_button,
            padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            alignment=ft.Alignment.CENTER,
            bgcolor=COLOR_BG,
            border=ft.Border(top=ft.BorderSide(1, "rgba(255,255,255,0.06)")),
        )

        self.root.controls = [
            header_bar,
            ft.Container(
                content=content,
                padding=ft.Padding.symmetric(horizontal=14, vertical=8),
                expand=True,
            ),
            bottom_bar,
        ]
        self._page.update()
