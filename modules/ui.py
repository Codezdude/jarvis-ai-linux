from modules.ai import get_active_provider, set_preferred_provider, get_latest_decision_trace
import math
import queue
import threading
import time
from typing import Any, Dict
import customtkinter
import customtkinter as ctk
import psutil

from modules.logger import log_error, log_info
from modules.approval import register_ui_prompt_callback
from modules.budget import register_budget_alert_callback
from modules.preferences import get_runtime_settings, update_runtime_settings

import re as _re

_CODE_BLOCK_RE = _re.compile(r"```[a-zA-Z0-9_+\-]*\n(.*?)```", _re.DOTALL)


def _extract_last_code_block(text: str):
    """Return the last fenced ```code``` block body in text, or None."""
    matches = _CODE_BLOCK_RE.findall(text or "")
    return matches[-1] if matches else None
from modules.reflections import get_reflections_for_review, mark_reflection_reviewed
from modules.voice import speak, set_voice_enabled, get_voice_enabled
from modules.memory import (
    get_preference,
    get_recent_history,
    get_runtime_state,
    init_db,
    save_interaction,
    save_preference,
    set_runtime_state,
    save_chat_message,
    delete_history_from_id,
    list_sessions,
)

# Configure global theme styling
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class JarvisHUD:
    """J.A.R.V.I.S. Quantum HUD – floating desktop interface with working input."""

    def __init__(self, root: ctk.CTk, command_callback=None, stop_callback=None, resume_callback=None):
        self.root = root
        self.command_callback = command_callback
        self.stop_callback = stop_callback
        self.resume_callback = resume_callback
        self.visible = bool(get_runtime_state("visible", True))
        self.state = str(get_runtime_state("ui_state", "idle"))
        self.angle = 0
        self.ui_queue = queue.Queue()
        self._chunk_queue = queue.Queue()
        self._live_assistant_buffer = None
        self._last_telemetry_update = 0.0
        self._animation_job = None
        self._queue_job = None

        # Initialize database memory
        init_db()

        # Customization state variables
        self.current_opacity = float(get_preference("opacity", get_runtime_state("opacity", 0.92)))
        self.show_telemetry = str(get_preference("telemetry_visible", get_runtime_state("show_telemetry", "true"))).lower() == "true"
        self.voice_enabled = str(get_preference("voice_enabled", str(get_voice_enabled()).lower())).lower() == "true"
        set_voice_enabled(self.voice_enabled)
        self.settings_visible = bool(get_runtime_state("settings_visible", False))
        self.history_visible = bool(get_runtime_state("history_visible", False))
        self.review_visible = bool(get_runtime_state("review_visible", False))
        self.fullscreen_mode = str(get_preference("fullscreen_mode", get_runtime_state("fullscreen_mode", "false"))).lower() == "true"
        self.auto_update_enabled = str(get_preference("auto_update_on_startup", "true")).lower() in ("1", "true", "yes", "on")
        self.scheduler_enabled = str(get_preference("scheduler_enabled", "true")).lower() in ("1", "true", "yes", "on")
        self.preferred_provider = str(get_preference("preferred_provider", get_runtime_state("preferred_provider", "auto")))
        if self.preferred_provider.lower() in {"auto", "automatic", "default", "none", ""}:
            self.preferred_provider = "auto"
        else:
            pass
        self._normal_geometry = None
        self._editing_message_id = None
        self._rendered_messages = []
        self._current_session_id = "default"
        self._settings_modal = None
        try:
            self._hud_always_on_top = bool(get_runtime_settings().get("hud_always_on_top", True))
        except Exception:
            self._hud_always_on_top = True
        set_runtime_state("voice_enabled", self.voice_enabled)
        set_runtime_state("show_telemetry", self.show_telemetry)
        set_runtime_state("fullscreen_mode", self.fullscreen_mode)
        set_runtime_state("ui_state", self.state)
        set_runtime_state("visible", self.visible)
        set_runtime_state("preferred_provider", self.preferred_provider)

        # Register HUD callbacks for approval prompts and budget alerts
        register_ui_prompt_callback(self._on_ui_prompt)
        register_budget_alert_callback(self._on_budget_alert)

        # Window Setup (floating shell)
        self.root.overrideredirect(False)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.current_opacity)

        # X11/Cinnamon focus fix: ensure keyboard input works on Linux Mint
        self.root.bind("<Button-1>", lambda event: self.root.focus_force())
        self.root.bind("<FocusIn>", lambda event: self.cmd_entry.focus_set() if hasattr(self, 'cmd_entry') else None)
        self.root.bind("<FocusOut>", lambda event: None)  # prevent focus steal on click-away

        width, height = 560, 650
        screen_w = self.root.winfo_screenwidth()
        x_pos = screen_w - width - 40
        y_pos = 36
        self.root.geometry(f"{width}x{height}+{x_pos}+{y_pos}")
        self.root.configure(fg_color="#07131d")

        self.root.bind("<Escape>", lambda event: self.hide_hud())
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        # --- HEADER ---
        header = ctk.CTkFrame(self.root, fg_color="#0d1d2a", corner_radius=14, height=42)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        header.bind("<ButtonPress-1>", self.start_move)
        header.bind("<B1-Motion>", self.do_move)

        title_lbl = ctk.CTkLabel(
            header, text="JARVIS // QUANTUM CORE",
            text_color="#dbeeff", font=("DejaVu Sans", 12, "bold")
        )
        title_lbl.pack(side="left", padx=14, pady=8)
        title_lbl.bind("<ButtonPress-1>", self.start_move)
        title_lbl.bind("<B1-Motion>", self.do_move)

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.pack(side="right", padx=(0, 10), pady=7)

        new_chat_btn = ctk.CTkButton(
            actions, text="+", width=30, height=28, fg_color="transparent",
            text_color="#9ef0c4", hover_color="#173b2f", font=("DejaVu Sans", 14, "bold"),
            command=self._new_chat
        )
        new_chat_btn.pack(side="right", padx=(0, 6))

        self.session_menu = ctk.CTkOptionMenu(
            actions, values=["default"], width=90, height=28,
            fg_color="#122738", text_color="#dbeeff",
            button_color="#183a59", dropdown_fg_color="#071018", dropdown_text_color="#dbeeff",
            font=("DejaVu Sans", 9), command=self._switch_session,
        )
        self.session_menu.pack(side="right", padx=(0, 6))

        self.settings_btn = ctk.CTkButton(
            actions, text="\u2699", width=30, height=28, fg_color="transparent",
            text_color="#9ed5ff", hover_color="#183047", font=("DejaVu Sans", 11, "bold"),
            command=self._open_settings_modal
        )
        self.settings_btn.pack(side="right", padx=(0, 6))

        history_btn = ctk.CTkButton(
            actions, text="\u25eb", width=30, height=28, fg_color="transparent",
            text_color="#9ef0c4", hover_color="#173b2f", font=("DejaVu Sans", 11, "bold"),
            command=self.toggle_history_panel
        )
        history_btn.pack(side="right", padx=(0, 6))

        review_btn = ctk.CTkButton(
            actions, text="\u2699\ufe0f", width=30, height=28, fg_color="transparent",
            text_color="#ffd700", hover_color="#3d3200", font=("DejaVu Sans", 11, "bold"),
            command=self.toggle_review_panel
        )
        review_btn.pack(side="right", padx=(0, 6))

        stop_btn = ctk.CTkButton(
            actions, text="\u23f9", width=30, height=28, fg_color="transparent",
            text_color="#ff6b6b", hover_color="#3d1a1a", font=("DejaVu Sans", 12, "bold"),
            command=self._on_stop_click
        )
        stop_btn.pack(side="right", padx=(0, 6))

        resume_btn = ctk.CTkButton(
            actions, text="\u25b6", width=30, height=28, fg_color="transparent",
            text_color="#9ef0c4", hover_color="#173b2f", font=("DejaVu Sans", 12, "bold"),
            command=self._on_resume_click
        )
        resume_btn.pack(side="right", padx=(0, 6))

        hide_btn = ctk.CTkButton(
            actions, text="\u2014", width=30, height=28, fg_color="transparent",
            text_color="#dbeeff", hover_color="#1a2d3d", font=("DejaVu Sans", 11, "bold"),
            command=self.hide_hud
        )
        hide_btn.pack(side="right", padx=(0, 6))

        close_btn = ctk.CTkButton(
            actions, text="\u2715", width=30, height=28, fg_color="transparent",
            text_color="#ff9d9d", hover_color="#341d1d", font=("DejaVu Sans", 11, "bold"),
            command=self.root.quit
        )
        close_btn.pack(side="right")

        # --- CORE PANEL ---
        self.body = ctk.CTkFrame(self.root, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.body.grid_columnconfigure(1, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.canvas = ctk.CTkCanvas(self.body, width=92, height=92, bg="#07131d", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="n", padx=(0, 10), pady=(0, 8))
        self.canvas.bind("<ButtonPress-1>", self.start_move)
        self.canvas.bind("<B1-Motion>", self.do_move)

        main_panel = ctk.CTkFrame(
            self.body, fg_color="#0d1d2a", corner_radius=18,
            border_width=1, border_color="#1f3350"
        )
        main_panel.grid(row=0, column=1, sticky="nsew")
        main_panel.grid_columnconfigure(0, weight=1)
        main_panel.grid_rowconfigure(2, weight=1)

        self.status_var = ctk.StringVar(value="SYSTEMS ONLINE")
        self.status_label = ctk.CTkLabel(
            main_panel, textvariable=self.status_var, text_color="#92d2ff",
            font=("DejaVu Sans", 10, "bold")
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))

        self.provider_status_var = ctk.StringVar(value="Provider: AUTO")
        self.provider_status_label = ctk.CTkLabel(
            main_panel, textvariable=self.provider_status_var, text_color="#9ef0c4",
            font=("DejaVu Sans", 9)
        )
        self.provider_status_label.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 8))

        self.response_box = ctk.CTkTextbox(
            main_panel, fg_color="#071018", text_color="#deefff",
            font=("DejaVu Sans", 10), corner_radius=12, border_spacing=12,
            border_width=1, border_color="#18324e", activate_scrollbars=True
        )
        self.response_box.insert("0.0", "Quantum core online. Systems initialized. Awaiting request...")
        self.response_box.configure(state="disabled")
        self.response_box.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))

        # --- INPUT SECTION ---
        input_frame = ctk.CTkFrame(main_panel, fg_color="transparent")
        input_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        input_frame.grid_columnconfigure(0, weight=1)

        self.cmd_entry = ctk.CTkEntry(
            input_frame, fg_color="#071018", text_color="#deefff",
            placeholder_text="Ask JARVIS something...", placeholder_text_color="#67839a",
            font=("DejaVu Sans", 10), border_width=1, border_color="#214b6b", height=38, corner_radius=10
        )
        self.cmd_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.cmd_entry.bind("<Return>", self.on_submit_text)
        self.cmd_entry.bind("<Button-1>", lambda event: self.cmd_entry.focus_set())
        self.cmd_entry.bind("<Up>", self._recall_last_user_message)
        self.cmd_entry.bind("<Escape>", self._cancel_edit_message)

        send_btn = ctk.CTkButton(
            input_frame, text="RUN", width=70, height=38,
            fg_color="#8dd4ff", text_color="#07131d", hover_color="#b0e5ff",
            font=("DejaVu Sans", 10, "bold"), corner_radius=10, command=self.on_submit_text
        )
        send_btn.grid(row=0, column=1, padx=(0, 6))

        mic_btn = ctk.CTkButton(
            input_frame, text="\U0001f3a4", width=44, height=38,
            fg_color="#9ef0c4", text_color="#07131d", hover_color="#c7f9dd",
            font=("DejaVu Sans", 11, "bold"), corner_radius=10, command=self.on_mic_click
        )
        mic_btn.grid(row=0, column=2)

        copy_code_btn = ctk.CTkButton(
            input_frame, text="\U0001f4cb", width=44, height=38,
            fg_color="#122738", text_color="#9ed5ff", hover_color="#183a59",
            font=("DejaVu Sans", 12, "bold"), corner_radius=10,
            command=self._copy_last_code_block
        )
        copy_code_btn.grid(row=0, column=3, padx=(6, 0))

        edit_btn = ctk.CTkButton(
            input_frame, text="\u270e", width=44, height=38,
            fg_color="#122738", text_color="#ffd700", hover_color="#3d3200",
            font=("DejaVu Sans", 12, "bold"), corner_radius=10,
            command=self._begin_edit_last_user_message
        )
        edit_btn.grid(row=0, column=4, padx=(6, 0))

        self.root.bind("<Control-Shift-C>", lambda e: self._copy_last_code_block())

        # --- SETTINGS PANEL ---
        self.settings_frame = ctk.CTkFrame(
            self.root, fg_color="#0d1d2a", corner_radius=14, border_width=1, border_color="#214b6b"
        )
        ctk.CTkLabel(self.settings_frame, text="HUD CONFIG", text_color="#9ed5ff", font=("DejaVu Sans", 10, "bold")).pack(pady=(10, 6))

        op_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        op_frame.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(op_frame, text="Opacity", text_color="#dbeeff", font=("DejaVu Sans", 9)).pack(side="left")
        for alpha_val in [0.75, 0.9, 1.0]:
            ctk.CTkButton(
                op_frame, text=f"{int(alpha_val * 100)}%", width=46, height=24,
                fg_color="#122738", text_color="#9ed5ff", hover_color="#183a59",
                font=("DejaVu Sans", 8, "bold"), corner_radius=8,
                command=lambda a=alpha_val: self.set_opacity(a)
            ).pack(side="right", padx=2)

        self.telemetry_toggle_btn = ctk.CTkButton(
            self.settings_frame, text="Telemetry: ON", height=28,
            fg_color="#122738", text_color="#9ef0c4", hover_color="#173b2f",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self.toggle_telemetry
        )
        self.telemetry_toggle_btn.pack(fill="x", padx=10, pady=(6, 4))

        self.voice_toggle_btn = ctk.CTkButton(
            self.settings_frame, text="Voice: ON", height=28,
            fg_color="#122738", text_color="#9ef0c4", hover_color="#173b2f",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self.toggle_voice_feedback
        )
        self.voice_toggle_btn.pack(fill="x", padx=10, pady=4)

        self.fullscreen_toggle_btn = ctk.CTkButton(
            self.settings_frame, text="Fullscreen: OFF", height=28,
            fg_color="#122738", text_color="#9ed5ff", hover_color="#183a59",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self.toggle_fullscreen
        )
        self.fullscreen_toggle_btn.pack(fill="x", padx=10, pady=(4, 8))

        provider_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        provider_frame.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(provider_frame, text="Provider", text_color="#dbeeff", font=("DejaVu Sans", 9)).pack(side="left")
        self.provider_menu = ctk.CTkOptionMenu(
            provider_frame,
            values=["Auto", "Groq", "Gemini", "Kilo", "Cloudflare", "AGY", "Ollama"],
            width=120, height=28,
            fg_color="#122738", text_color="#dbeeff",
            button_color="#183a59", dropdown_fg_color="#071018", dropdown_text_color="#dbeeff",
            font=("DejaVu Sans", 9), command=self.set_preferred_provider_for_ui,
        )
        self.provider_menu.pack(side="right")
        self.provider_menu.set("Auto" if self.preferred_provider.lower() == "auto" else self.preferred_provider.title())

        self.auto_update_toggle_btn = ctk.CTkButton(
            self.settings_frame, text="Auto-Update on Startup: ON", height=28,
            fg_color="#122738", text_color="#9ef0c4", hover_color="#173b2f",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self.toggle_auto_update
        )
        self.auto_update_toggle_btn.pack(fill="x", padx=10, pady=(4, 4))

        self.scheduler_toggle_btn = ctk.CTkButton(
            self.settings_frame, text="Scheduler: ON", height=28,
            fg_color="#122738", text_color="#9ef0c4", hover_color="#173b2f",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self.toggle_scheduler
        )
        self.scheduler_toggle_btn.pack(fill="x", padx=10, pady=4)

        action_row = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        action_row.pack(fill="x", padx=10, pady=(4, 8))

        ctk.CTkButton(
            action_row, text="Check Updates", height=28,
            fg_color="#122738", text_color="#9ed5ff", hover_color="#183a59",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self._on_check_updates_click
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            action_row, text="Clear History", height=28,
            fg_color="#122738", text_color="#ff9d9d", hover_color="#341d1d",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self._on_clear_history_click
        ).pack(side="left")

        if self.show_telemetry:
            self.telemetry_toggle_btn.configure(text="Telemetry: ON", text_color="#9ef0c4")
        else:
            self.telemetry_toggle_btn.configure(text="Telemetry: OFF", text_color="#ff9d9d")

        if self.voice_enabled:
            self.voice_toggle_btn.configure(text="Voice: ON", text_color="#9ef0c4")
        else:
            self.voice_toggle_btn.configure(text="Voice: OFF", text_color="#ff9d9d")

        if self.fullscreen_mode:
            self.fullscreen_toggle_btn.configure(text="Fullscreen: ON", text_color="#9ef0c4")
            self._normal_geometry = self.root.geometry()
            self.root.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")

        if self.auto_update_enabled:
            self.auto_update_toggle_btn.configure(text="Auto-Update on Startup: ON", text_color="#9ef0c4")
        else:
            self.auto_update_toggle_btn.configure(text="Auto-Update on Startup: OFF", text_color="#ff9d9d")

        if self.scheduler_enabled:
            self.scheduler_toggle_btn.configure(text="Scheduler: ON", text_color="#9ef0c4")
        else:
            self.scheduler_toggle_btn.configure(text="Scheduler: OFF", text_color="#ff9d9d")

        # --- HISTORY PANEL ---
        self.history_frame = ctk.CTkFrame(
            self.root, fg_color="#0d1d2a", corner_radius=14, border_width=1, border_color="#7ae6c9"
        )
        ctk.CTkLabel(self.history_frame, text="MEMORY LOG", text_color="#9ef0c4", font=("DejaVu Sans", 10, "bold")).pack(pady=(10, 4))

        self.history_box = ctk.CTkTextbox(
            self.history_frame, height=120, fg_color="#071018", text_color="#dbeeff",
            font=("DejaVu Sans", 9), border_spacing=8, activate_scrollbars=True
        )
        self.history_box.pack(fill="both", expand=True, padx=8, pady=4)
        self.history_box.configure(state="disabled")

        ctk.CTkButton(
            self.history_frame, text="Refresh log", height=28,
            fg_color="#122738", text_color="#9ef0c4", hover_color="#173b2f",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self.load_history_data
        ).pack(fill="x", padx=8, pady=(4, 10))

        # --- WEEKLY REVIEW PANEL ---
        self.review_frame = ctk.CTkFrame(
            self.root, fg_color="#0d1d2a", corner_radius=14, border_width=1, border_color="#ffd700"
        )
        ctk.CTkLabel(self.review_frame, text="WEEKLY REVIEW", text_color="#ffd700", font=("DejaVu Sans", 10, "bold")).pack(pady=(10, 4))

        self.review_box = ctk.CTkTextbox(
            self.review_frame, height=120, fg_color="#071018", text_color="#dbeeff",
            font=("DejaVu Sans", 9), border_spacing=8, activate_scrollbars=True
        )
        self.review_box.pack(fill="both", expand=True, padx=8, pady=4)
        self.review_box.configure(state="disabled")

        ctk.CTkButton(
            self.review_frame, text="Refresh", height=28,
            fg_color="#122738", text_color="#ffd700", hover_color="#3d3200",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self.load_review_data
        ).pack(fill="x", padx=8, pady=(4, 10))

        self.refresh_provider_status()
        self._hydrate_chat_history()
        try:
            sessions = list_sessions()
            if sessions:
                self.session_menu.configure(values=sessions)
                self.session_menu.set(self._current_session_id)
        except Exception:
            pass
        self.animate_rings()
        self.process_ui_queue()

    # --- DRAG WINDOW LOGIC ---
    def start_move(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def do_move(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    # --- PANEL TOGGLES ---
    def toggle_settings_panel(self):
        if self.history_visible:
            self.history_frame.grid_remove()
            self.history_visible = False
        if self.review_visible:
            self.review_frame.grid_remove()
            self.review_visible = False
        if self.settings_visible:
            self.settings_frame.grid_remove()
            self.settings_visible = False
        else:
            self.settings_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
            self.settings_visible = True
        set_runtime_state("settings_visible", self.settings_visible)

    def toggle_history_panel(self):
        if self.settings_visible:
            self.settings_frame.grid_remove()
            self.settings_visible = False
        if self.review_visible:
            self.review_frame.grid_remove()
            self.review_visible = False
        if self.history_visible:
            self.history_frame.grid_remove()
            self.history_visible = False
        else:
            self.history_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
            self.history_visible = True
            self.load_history_data()
        set_runtime_state("history_visible", self.history_visible)

    def toggle_review_panel(self):
        if self.settings_visible:
            self.settings_frame.grid_remove()
            self.settings_visible = False
        if self.history_visible:
            self.history_frame.grid_remove()
            self.history_visible = False
        if self.review_visible:
            self.review_frame.grid_remove()
            self.review_visible = False
        else:
            self.review_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
            self.review_visible = True
            self.load_review_data()
        set_runtime_state("review_visible", self.review_visible)

    # --- SETTINGS MODAL (⚙) ---
    def _open_settings_modal(self):
        """Open a dedicated settings dialog organized into four sections.

        Sections: Appearance, AI & Routing, Feedback, System. Persists routing
        preferences via modules.preferences and reuses the existing toggle
        handlers so the inline HUD panel stays in sync.
        """
        if getattr(self, "_settings_modal", None) is not None:
            try:
                self._settings_modal.focus()
                return
            except Exception:
                self._settings_modal = None

        try:
            settings = get_runtime_settings()
        except Exception:
            settings = {}

        modal = ctk.CTkToplevel(self.root)
        self._settings_modal = modal
        modal.title("JARVIS // SETTINGS")
        modal.geometry("560x720")
        modal.configure(fg_color="#0a141d")
        try:
            modal.transient(self.root)
            modal.after(50, modal.grab_set)
        except Exception:
            pass

        ctk.CTkLabel(
            modal, text="\u2699 SETTINGS", text_color="#9ed5ff",
            font=("DejaVu Sans", 14, "bold"),
        ).pack(pady=(14, 6))

        body = ctk.CTkScrollableFrame(modal, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        def _section(title, accent="#9ed5ff"):
            frame = ctk.CTkFrame(body, fg_color="#0d1d2a", corner_radius=12,
                                 border_width=1, border_color="#214b6b")
            frame.pack(fill="x", pady=6)
            ctk.CTkLabel(frame, text=title, text_color=accent,
                         font=("DejaVu Sans", 10, "bold")).pack(anchor="w", padx=12, pady=(8, 4))
            return frame

        def _toggle_button(parent, label, state_attr, handler, on_text, off_text):
            state = bool(getattr(self, state_attr))
            btn = ctk.CTkButton(
                parent, text=f"{label}: {'ON' if state else 'OFF'}", height=28,
                fg_color="#122738", text_color="#9ef0c4" if state else "#ff9d9d",
                hover_color="#173b2f", font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            )

            def _clicked():
                handler()
                new_state = bool(getattr(self, state_attr))
                btn.configure(text=f"{label}: {'ON' if new_state else 'OFF'}",
                              text_color="#9ef0c4" if new_state else "#ff9d9d")

            btn.configure(command=_clicked)
            btn.pack(fill="x", padx=12, pady=4)
            return btn

        # ── 1. APPEARANCE ───────────────────────────────────────────
        appear = _section("1 \u2022 APPEARANCE")
        op_row = ctk.CTkFrame(appear, fg_color="transparent")
        op_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(op_row, text="Opacity", text_color="#dbeeff",
                     font=("DejaVu Sans", 9)).pack(side="left")
        for alpha_val in [0.75, 0.9, 1.0]:
            ctk.CTkButton(
                op_row, text=f"{int(alpha_val * 100)}%", width=52, height=24,
                fg_color="#122738", text_color="#9ed5ff", hover_color="#183a59",
                font=("DejaVu Sans", 8, "bold"), corner_radius=8,
                command=lambda a=alpha_val: self.set_opacity(a),
            ).pack(side="right", padx=2)
        _toggle_button(appear, "Fullscreen", "fullscreen_mode", self.toggle_fullscreen,
                       "Fullscreen", "Fullscreen")
        self._modal_ontop_btn = _toggle_button(
            appear, "Always on Top", "_hud_always_on_top", self._toggle_always_on_top,
            "Always on Top", "Always on Top")

        # ── 2. AI & ROUTING ─────────────────────────────────────────
        routing = _section("2 \u2022 AI & ROUTING", accent="#7ae6c9")
        prov_row = ctk.CTkFrame(routing, fg_color="transparent")
        prov_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(prov_row, text="Provider", text_color="#dbeeff",
                     font=("DejaVu Sans", 9)).pack(side="left")
        self._modal_provider_menu = ctk.CTkOptionMenu(
            prov_row, values=["Auto", "Groq", "Gemini", "Kilo", "Cloudflare", "AGY", "Ollama"],
            width=140, height=28, fg_color="#122738", text_color="#dbeeff",
            button_color="#183a59", dropdown_fg_color="#071018", dropdown_text_color="#dbeeff",
            font=("DejaVu Sans", 9), command=self.set_preferred_provider_for_ui,
        )
        current_prov = (self.preferred_provider or "auto").lower()
        self._modal_provider_menu.set("Auto" if current_prov == "auto" else current_prov.title())
        self._modal_provider_menu.pack(side="right")

        route_row = ctk.CTkFrame(routing, fg_color="transparent")
        route_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(route_row, text="Routing mode", text_color="#dbeeff",
                     font=("DejaVu Sans", 9)).pack(side="left")
        self._modal_route_menu = ctk.CTkOptionMenu(
            route_row, values=["Auto", "Ollama only"], width=140, height=28,
            fg_color="#122738", text_color="#dbeeff", button_color="#183a59",
            dropdown_fg_color="#071018", dropdown_text_color="#dbeeff",
            font=("DejaVu Sans", 9),
            command=lambda v: update_runtime_settings(
                {"routing_mode": "ollama_only" if v == "Ollama only" else "auto"}),
        )
        self._modal_route_menu.set(
            "Ollama only" if settings.get("routing_mode") == "ollama_only" else "Auto")
        self._modal_route_menu.pack(side="right")

        cap_row = ctk.CTkFrame(routing, fg_color="transparent")
        cap_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(cap_row, text="Daily cloud token cap", text_color="#dbeeff",
                     font=("DejaVu Sans", 9)).pack(side="left")
        self._modal_cap_entry = ctk.CTkEntry(cap_row, width=120, height=28,
                                             fg_color="#122738", text_color="#dbeeff")
        try:
            self._modal_cap_entry.insert(0, str(int(settings.get("daily_cloud_token_cap", 250000))))
        except Exception:
            self._modal_cap_entry.insert(0, "250000")
        self._modal_cap_entry.pack(side="right")
        ctk.CTkButton(
            routing, text="Save routing", height=26, fg_color="#173b2f",
            text_color="#9ef0c4", hover_color="#1f5340",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self._save_routing_settings,
        ).pack(fill="x", padx=12, pady=(2, 8))

        # ── 3. FEEDBACK ─────────────────────────────────────────────
        feedback = _section("3 \u2022 FEEDBACK", accent="#ffd700")
        _toggle_button(feedback, "Telemetry", "show_telemetry", self.toggle_telemetry,
                       "Telemetry", "Telemetry")
        _toggle_button(feedback, "Voice", "voice_enabled", self.toggle_voice_feedback,
                       "Voice", "Voice")

        # ── 4. SYSTEM ───────────────────────────────────────────────
        system = _section("4 \u2022 SYSTEM", accent="#ff9d9d")
        _toggle_button(system, "Auto-Update on Startup", "auto_update_enabled",
                       self.toggle_auto_update, "Auto-Update", "Auto-Update")
        _toggle_button(system, "Scheduler", "scheduler_enabled", self.toggle_scheduler,
                       "Scheduler", "Scheduler")
        btn_row = ctk.CTkFrame(system, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=8)
        ctk.CTkButton(
            btn_row, text="Check Updates", height=28, fg_color="#122738",
            text_color="#9ed5ff", hover_color="#183a59",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self._on_check_updates_click,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            btn_row, text="Clear History", height=28, fg_color="#341d1d",
            text_color="#ff9d9d", hover_color="#4a2626",
            font=("DejaVu Sans", 9, "bold"), corner_radius=8,
            command=self._on_clear_history_click,
        ).pack(side="left")

        # ── footer ──────────────────────────────────────────────────
        footer = ctk.CTkFrame(modal, fg_color="transparent")
        footer.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(
            footer, text="Close", height=30, fg_color="#183a59", text_color="#dbeeff",
            hover_color="#214b6b", font=("DejaVu Sans", 10, "bold"), corner_radius=8,
            command=self._close_settings_modal,
        ).pack(fill="x")

    def _close_settings_modal(self):
        modal = getattr(self, "_settings_modal", None)
        if modal is None:
            return
        try:
            modal.grab_release()
            modal.destroy()
        except Exception:
            pass
        self._settings_modal = None

    def _toggle_always_on_top(self):
        new_state = not bool(getattr(self, "_hud_always_on_top", False))
        self._hud_always_on_top = new_state
        try:
            self.root.attributes("-topmost", new_state)
        except Exception:
            pass
        try:
            update_runtime_settings({"hud_always_on_top": new_state})
        except Exception:
            pass

    def _save_routing_settings(self):
        try:
            raw = self._modal_cap_entry.get().strip()
            cap = max(0, int(raw)) if raw else 250000
        except Exception:
            cap = 250000
        route = "ollama_only" if self._modal_route_menu.get() == "Ollama only" else "auto"
        try:
            update_runtime_settings({"daily_cloud_token_cap": cap, "routing_mode": route})
            self.update_ui("SETTINGS SAVED", "idle",
                           f"Routing saved (cap={cap}, mode={route}).")
        except Exception as e:
            log_error(f"Failed to save routing settings: {e}")
            self.update_ui("SAVE ERROR", "idle", f"Failed to save settings: {e}")

    def load_review_data(self):
        reflections = get_reflections_for_review(
            unreviewed_only=True, failures_only=True, limit=20
        )
        self.review_box.configure(state="normal")
        self.review_box.delete("0.0", "end")
        if not reflections:
            self.review_box.insert("0.0", "No unreviewed failures in the corpus.")
        else:
            for r in reflections:
                fid = r.get("id", "?")
                mode = r.get("failure_mode", "unknown")
                score = r.get("overall_score", 0.0)
                cause = r.get("root_cause", "")
                remedy = r.get("proposed_remediation", "none")
                self.review_box.insert(
                    "end",
                    f"[#{fid}] mode={mode} score={score}\n"
                    f"  cause: {cause}\n"
                    f"  remediation: {remedy}\n"
                    f"  [Mark Reviewed] via mark_reflection_reviewed({fid})\n"
                    + "\u2500"*38 + "\n",
                )
        self.review_box.configure(state="disabled")

    def mark_reviewed(self, reflection_id: int):
        ok = mark_reflection_reviewed(int(reflection_id), actor="user")
        if ok:
            self.load_review_data()
            self.update_ui("REVIEW UPDATED", "idle", f"Reflection {reflection_id} marked reviewed.")

    def _on_stop_click(self):
        """Handler for the HUD STOP button."""
        if self.stop_callback is not None:
            self.stop_callback()
        self.update_ui("STOPPED", "idle", "All autonomous subsystems halted.")

    def _on_resume_click(self):
        """Handler for the HUD RESUME button."""
        if self.resume_callback is not None:
            self.resume_callback()
        self.update_ui("RESUMED", "idle", "Autonomous subsystems resumed.")

    def _append_chat_bubble(self, role, content, message_id=None, badge=None):
        """Append a role/content bubble to the response box and track it for edit/resend."""
        self.response_box.configure(state="normal")
        who = "YOU" if role == "user" else "JARVIS"
        marker = f"{who}: {content}\n"
        self.response_box.insert("end", marker)
        if badge:
            self._insert_dim_line(badge)
        self.response_box.insert("end", "\u2500" * 30 + "\n")
        self.response_box.see("end")
        self.response_box.configure(state="disabled")
        self._rendered_messages.append(
            {"id": message_id, "role": role, "content": content, "widget": None}
        )

    def _insert_dim_line(self, text: str):
        """Insert a dim-colored annotation line (falls back to plain text)."""
        try:
            self.response_box._textbox.tag_configure("dim", foreground="#5d7a92")
            self.response_box._textbox.insert("end", text + "\n", "dim")
        except Exception:
            self.response_box.insert("end", text + "\n")

    def _latest_tool_badge(self):
        """Return a dim badge string listing tools invoked in the latest turn, or None."""
        try:
            trace = get_latest_decision_trace()
        except Exception:
            trace = None
        if not trace:
            return None
        if isinstance(trace, dict):
            tools = trace.get("tools_invoked")
        else:
            tools = getattr(trace, "tools_invoked", None)
        if not tools:
            return None
        unique = list(dict.fromkeys(tools))
        return "\u2699 tools invoked: " + ", ".join(str(t) for t in unique)

    def _copy_last_code_block(self):
        """Copy the last fenced code block from the most recent assistant reply."""
        code = None
        for msg in reversed(self._rendered_messages):
            if msg.get("role") != "assistant":
                continue
            code = _extract_last_code_block(msg.get("content", ""))
            if code is not None:
                break
        if code is None:
            self.update_ui("NO CODE", "idle", "No code block found in the last reply.")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(code)
            self.update_ui("CODE COPIED", "idle", "Code block copied to clipboard.")
        except Exception as e:
            self.update_ui("COPY ERROR", "idle", f"Copy failed: {e}")

    def _hydrate_chat_history(self):
        """Load prior messages for the current session into the response box on boot."""
        try:
            history = get_recent_history(limit=40, session_id=self._current_session_id)
        except Exception:
            history = []
        if not history:
            return
        self.response_box.configure(state="normal")
        self.response_box.delete("0.0", "end")
        self.response_box.configure(state="disabled")
        self._rendered_messages = []
        for item in history:
            self._append_chat_bubble(
                role=item.get("role", "assistant"),
                content=item.get("content", ""),
                message_id=item.get("id"),
            )

    def _begin_edit_message(self, message_id, content):
        """Load a prior user message back into the input for editing/resending."""
        self._editing_message_id = message_id
        self.cmd_entry.delete(0, "end")
        self.cmd_entry.insert(0, content)
        self.cmd_entry.focus_set()

    def _cancel_edit_message(self, event=None):
        self._editing_message_id = None
        self.cmd_entry.delete(0, "end")

    def _recall_last_user_message(self, event=None):
        """Recall the last user message when the input is empty (Up arrow)."""
        if self.cmd_entry.get().strip():
            return "break"
        for item in reversed(self._rendered_messages):
            if item["role"] == "user":
                self._begin_edit_message(item["id"], item["content"])
                break
        return "break"

    def _begin_edit_last_user_message(self):
        """✎ button: load the most recent user message into the input for editing."""
        for item in reversed(self._rendered_messages):
            if item.get("role") == "user":
                self._begin_edit_message(item["id"], item["content"])
                return
        self.update_ui("NO MESSAGE", "idle", "No user message to edit yet.")

    def _new_chat(self):
        """Start a fresh timestamped session and reset the view."""
        import time as _time

        self._current_session_id = f"chat_{int(_time.time())}"
        self._rendered_messages = []
        self._editing_message_id = None
        self.response_box.configure(state="normal")
        self.response_box.delete("0.0", "end")
        self.response_box.configure(state="disabled")
        self.update_ui("NEW CHAT", "idle", f"Started session {self._current_session_id}.")

    def _switch_session(self, session_id):
        """Switch the active chat session and reload its history."""
        self._current_session_id = session_id or "default"
        self._hydrate_chat_history()
        self.update_ui("SESSION", "idle", f"Switched to {self._current_session_id}.")

    def load_history_data(self):
        records = get_recent_history(limit=15)
        self.history_box.configure(state="normal")
        self.history_box.delete("0.0", "end")
        if not records:
            self.history_box.insert("0.0", "No archival logs recorded yet.")
        else:
            for rec in records:
                if isinstance(rec, dict):
                    ts = rec.get("timestamp", "")
                    role = rec.get("role", "")
                    content = rec.get("content", "")
                    who = "USER" if role == "user" else "JARVIS"
                    self.history_box.insert("end", f"[{ts}]\n{who}: {content}\n" + "\u2500"*38 + "\n")
                else:
                    timestamp, query, response = rec
                    self.history_box.insert("end", f"[{timestamp}]\nUSER: {query}\nJARVIS: {response}\n" + "\u2500"*38 + "\n")

        # Render latest DecisionTrace badge if available
        trace = get_latest_decision_trace()
        if trace:
            self.history_box.insert("end", "\n[TRACE] Last AI decision provenance:\n")
            self.history_box.insert("end", f"  provider={trace.get('provider', 'unknown')} model={trace.get('model', 'unknown')}\n")
            self.history_box.insert("end", f"  memory_ids={trace.get('memory_ids_retrieved', [])}\n")
            self.history_box.insert("end", f"  preference_keys={trace.get('preference_keys_used', [])}\n")
            self.history_box.insert("end", f"  tools_invoked={trace.get('tools_invoked', [])}\n")
            self.history_box.insert("end", f"  audit_event_ids={trace.get('audit_event_ids', [])}\n")
        self.history_box.configure(state="disabled")

    def set_opacity(self, alpha_val: float):
        self.current_opacity = alpha_val
        save_preference("opacity", alpha_val)
        self.root.attributes("-alpha", alpha_val)

    def toggle_telemetry(self):
        self.show_telemetry = not self.show_telemetry
        set_runtime_state("show_telemetry", self.show_telemetry)
        save_preference("telemetry_visible", self.show_telemetry)
        if self.show_telemetry:
            self.telemetry_toggle_btn.configure(text="Telemetry Display: ON", text_color="#00ff88")
        else:
            self.telemetry_toggle_btn.configure(text="Telemetry Display: OFF", text_color="#ff4444")

    def toggle_voice_feedback(self):
        self.voice_enabled = not self.voice_enabled
        set_voice_enabled(self.voice_enabled)
        save_preference("voice_enabled", self.voice_enabled)
        set_runtime_state("voice_enabled", self.voice_enabled)
        if self.voice_enabled:
            self.voice_toggle_btn.configure(text="Voice Feedback: ON", text_color="#00ff88")
        else:
            self.voice_toggle_btn.configure(text="Voice Feedback: OFF", text_color="#ff4444")

    def toggle_fullscreen(self):
        self.fullscreen_mode = not self.fullscreen_mode
        set_runtime_state("fullscreen_mode", self.fullscreen_mode)
        save_preference("fullscreen_mode", self.fullscreen_mode)

        if self.fullscreen_mode:
            self._normal_geometry = self.root.geometry()
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            self.root.geometry(f"{screen_w}x{screen_h}+0+0")
            self.fullscreen_toggle_btn.configure(text="Fullscreen: ON", text_color="#00ff88")
        else:
            if self._normal_geometry:
                self.root.geometry(self._normal_geometry)
            self.fullscreen_toggle_btn.configure(text="Fullscreen: OFF", text_color="#00f3ff")

    def toggle_auto_update(self):
        """Toggle automatic update-on-startup preference."""
        self.auto_update_enabled = not self.auto_update_enabled
        save_preference("auto_update_on_startup", self.auto_update_enabled)
        if self.auto_update_enabled:
            self.auto_update_toggle_btn.configure(text="Auto-Update on Startup: ON", text_color="#9ef0c4")
        else:
            self.auto_update_toggle_btn.configure(text="Auto-Update on Startup: OFF", text_color="#ff9d9d")

    def toggle_scheduler(self):
        """Toggle the background scheduler (morning briefings)."""
        self.scheduler_enabled = not self.scheduler_enabled
        save_preference("scheduler_enabled", self.scheduler_enabled)
        if self.scheduler_enabled:
            self.scheduler_toggle_btn.configure(text="Scheduler: ON", text_color="#9ef0c4")
        else:
            self.scheduler_toggle_btn.configure(text="Scheduler: OFF", text_color="#ff9d9d")

    def _on_check_updates_click(self):
        """Trigger a manual update check in a background thread."""
        def _do_update():
            try:
                from modules.self_update import self_update_system
                result = self_update_system(pull_git=True, restart_after=False)
                log_info(f"Manual update result:\n{result}")
                self.update_ui("UPDATE CHECKED", "idle", result)
            except Exception as e:
                log_error(f"Manual update failed: {e}")
                self.update_ui("UPDATE ERROR", "idle", f"Update failed: {e}")

        threading.Thread(target=_do_update, daemon=True).start()
        self.update_ui("CHECKING UPDATES", "thinking", "Checking for updates from Git...")

    def _on_clear_history_click(self):
        """Clear all conversation history and reset to default system prompt."""
        try:
            from modules.ai import clear_conversation_history
            clear_conversation_history()
            self.update_ui("HISTORY CLEARED", "idle", "Conversation history has been cleared.")
        except Exception as e:
            log_error(f"Failed to clear history: {e}")
            self.update_ui("ERROR", "idle", f"Failed to clear history: {e}")

    def set_preferred_provider_for_ui(self, selected_value: str):
        value = (selected_value or "Auto").strip()
        normalized = value.lower()
        if normalized in {"auto", "automatic", "default", "none"}:
            self.preferred_provider = "auto"
            set_runtime_state("preferred_provider", "auto")
            save_preference("preferred_provider", "auto")
            self.refresh_provider_status()
            return

        mapping = {
            "groq": "groq",
            "gemini": "gemini",
            "kilo": "kilo",
            "cloudflare": "cloudflare",
            "agy": "agy",
            "ollama": "ollama",
        }

        self.preferred_provider = mapping.get(normalized, normalized)
        set_runtime_state("preferred_provider", self.preferred_provider)
        save_preference("preferred_provider", self.preferred_provider)
        self.refresh_provider_status()

    def toggle_hud(self):
        if self.visible:
            self.hide_hud()
        else:
            self.show_hud()

    def hide_hud(self):
        self.root.withdraw()
        self.visible = False
        set_runtime_state("visible", False)

    def show_hud(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.root.after(50, lambda: self.cmd_entry.focus_set())
        self.visible = True
        set_runtime_state("visible", True)

    def refresh_provider_status(self):
        active = get_runtime_state("active_provider", "auto")
        self.provider_status_var.set(f"Provider: {active.upper() if active and active.lower() != 'auto' else 'AUTO'}")

    def update_ui(self, status_text: str, state: str, reply_text: str = None):
        """Thread-safe: queue a UI update to be processed on the main thread."""
        self.ui_queue.put((status_text, state, reply_text))
        if self._queue_job is None:
            delay = 60 if state in {"thinking", "speaking"} else 120
            self._queue_job = self.root.after(delay, self.process_ui_queue)

    # --- LIVE STREAMING (progressive assistant bubble) ---
    def push_chunk(self, delta: str):
        """Thread-safe: enqueue a chunk of assistant text for live rendering."""
        if not delta:
            return
        self._chunk_queue.put(delta)
        if self._queue_job is None:
            self._queue_job = self.root.after(40, self.process_ui_queue)

    def _drain_chunks(self):
        """Append any queued stream chunks to the live assistant bubble."""
        while True:
            try:
                delta = self._chunk_queue.get_nowait()
            except queue.Empty:
                break
            self._append_live_text(delta)

    def _append_live_text(self, delta: str):
        """Append text to the in-progress assistant bubble, creating it if needed."""
        self.response_box.configure(state="normal")
        if self._live_assistant_buffer is None:
            self.response_box.insert("end", "JARVIS: ")
            self._live_assistant_buffer = ""
        self.response_box.insert("end", delta)
        self._live_assistant_buffer += delta
        self.response_box.see("end")
        self.response_box.configure(state="disabled")

    def _close_live_assistant(self, final_text: str = None):
        """Finalize the live assistant bubble with a separator and persist it."""
        if self._live_assistant_buffer is None:
            return
        if final_text and not self._live_assistant_buffer.strip():
            self.response_box.configure(state="normal")
            self.response_box.insert("end", final_text)
            self._live_assistant_buffer = final_text
            self.response_box.configure(state="disabled")
        self.response_box.configure(state="normal")
        badge = self._latest_tool_badge()
        if badge:
            self.response_box.insert("end", "\n")
            self._insert_dim_line(badge)
        self.response_box.insert("end", "\n" + "\u2500" * 30 + "\n")
        self.response_box.see("end")
        self.response_box.configure(state="disabled")
        content = self._live_assistant_buffer
        self._live_assistant_buffer = None
        self._rendered_messages.append(
            {"id": None, "role": "assistant", "content": content, "widget": None}
        )
        sess = self._current_session_id
        try:
            save_chat_message("assistant", content, session_id=sess)
        except Exception:
            pass
        try:
            from modules.memory_consolidation import compact_session_if_needed

            threading.Thread(
                target=lambda s=sess: compact_session_if_needed(session_id=s),
                daemon=True,
            ).start()
        except Exception:
            pass

    def process_ui_queue(self):
        self._queue_job = None
        self._drain_chunks()
        while not self.ui_queue.empty():
            status_text, state, reply_text = self.ui_queue.get()
            self.status_var.set(status_text)
            self.state = state
            set_runtime_state("ui_state", self.state)
            self.refresh_provider_status()
            if reply_text is not None:
                # "> query" echoes are user bubbles; everything else is an assistant bubble
                if reply_text.startswith("> "):
                    self._append_chat_bubble("user", reply_text[2:])
                elif self._live_assistant_buffer is not None:
                    # A live stream produced this reply; close the bubble instead of duplicating
                    self._close_live_assistant(reply_text)
                else:
                    self._append_chat_bubble("assistant", reply_text, badge=self._latest_tool_badge())
                    # Persist assistant reply to the active session + compact if needed
                    sess = self._current_session_id
                    try:
                        save_chat_message("assistant", reply_text, session_id=sess)
                    except Exception:
                        pass
                    try:
                        from modules.memory_consolidation import compact_session_if_needed

                        threading.Thread(
                            target=lambda s=sess: compact_session_if_needed(session_id=s),
                            daemon=True,
                        ).start()
                    except Exception:
                        pass
        if self.visible and (not self.ui_queue.empty() or not self._chunk_queue.empty()):
            self._queue_job = self.root.after(80, self.process_ui_queue)

    def on_submit_text(self, event=None):
        query = self.cmd_entry.get().strip()
        if not query:
            return
        # Handle edit/resend: rewind history from the edited message onward
        if self._editing_message_id is not None:
            target_id = self._editing_message_id
            try:
                delete_history_from_id(target_id, session_id=self._current_session_id)
            except Exception:
                pass
            cutoff = next(
                (i for i, m in enumerate(self._rendered_messages) if m["id"] == target_id),
                None,
            )
            if cutoff is not None:
                self._rendered_messages = self._rendered_messages[:cutoff]
            self._editing_message_id = None
            try:
                self._hydrate_chat_history()
            except Exception:
                pass
        self.cmd_entry.delete(0, "end")

        # Persist the user message to the active session
        try:
            save_chat_message("user", query, session_id=self._current_session_id)
        except Exception:
            pass

        self.update_ui("PROCESSING", "thinking", f"> {query}")

        if self.command_callback:
            threading.Thread(
                target=self.command_callback,
                args=(query, self.update_ui, self.push_chunk),
                daemon=True,
            ).start()

    def on_mic_click(self):
        def background_listen():
            try:
                from modules.listener import listen_to_user
                query = listen_to_user(self.update_ui)
                if query:
                    self.update_ui("PROCESSING", "thinking", f"> {query}")
                    if self.command_callback:
                        self.command_callback(query, self.update_ui, self.push_chunk)
                else:
                    self.update_ui("STANDBY // LISTENING", "idle", "Audio input unclear, Sir.")
            except Exception as e:
                log_error(f"Microphone input error: {e}")
                self.update_ui("ERROR", "idle", f"Voice input unavailable: {e}")

        threading.Thread(target=background_listen, daemon=True).start()

    # --- HUD CALLBACKS (approval prompts & budget alerts) ---
    def _format_approval_prompt(self, payload: Dict[str, Any]) -> str:
        """Build the human-readable approval prompt text, including a diff if present."""
        actor = payload.get("actor", "unknown")
        action = payload.get("action", "action")
        target = payload.get("target", "")
        reason = payload.get("reason", "")
        lines = [
            f"Actor: {actor}",
            f"Action: {action}",
            f"Target: {target}",
            f"Reason: {reason}",
        ]
        diff = payload.get("diff")
        if diff:
            excerpt = diff if len(diff) <= 1800 else diff[:1800] + "\n... (diff truncated)"
            lines.append("\nProposed changes (unified diff):\n" + excerpt)
        lines.append("\nAllow?")
        return "\n".join(lines)

    def _on_ui_prompt(self, payload: Dict[str, Any]) -> bool:
        """Modal confirmation handler for approval gate UI prompts."""
        import tkinter.messagebox as msgbox
        confirmed = msgbox.askyesno(
            "JARVIS Approval Required",
            self._format_approval_prompt(payload),
        )
        return bool(confirmed)

    def _on_budget_alert(self, payload: Dict[str, Any]) -> None:
        """HUD notification handler for budget limit / breaker trips."""
        actor = payload.get("actor", "unknown")
        reason = payload.get("reason", "")
        amount = payload.get("amount", 0)
        self.update_ui("BUDGET ALERT", "warning", f"Budget alert: {actor} — {reason} ({amount})")

    # --- WOBBLY VORTEX CANVAS ANIMATION ---
    def animate_rings(self):
        if self._animation_job is not None:
            return

        if not self.visible:
            self._animation_job = self.root.after(200, self.animate_rings)
            return

        self._animation_job = self.root.after(120, self._animate_rings_frame)

    def _animate_rings_frame(self):
        self._animation_job = None
        if not self.visible:
            return

        self.canvas.delete("all")
        cx, cy = 46, 46

        palette = {
            "idle": ("#00f3ff", "#005577"),
            "listening": ("#00ff88", "#006633"),
            "thinking": ("#ffaa00", "#884400"),
            "speaking": ("#38bdf8", "#0284c7"),
        }
        primary, secondary = palette.get(self.state, ("#00f3ff", "#005577"))

        speed = 4 if self.state in ("thinking", "speaking") else 1
        self.angle = (self.angle + speed) % 360

        wobble_factor = 5 if self.state in ("thinking", "speaking", "listening") else 2.0

        for i in range(3):
            offset_angle = self.angle + (i * 50)
            r_x = 30 + (i * 7) + math.sin(math.radians(offset_angle * 2)) * wobble_factor
            r_y = 22 + (i * 5) + math.cos(math.radians(offset_angle * 2)) * wobble_factor

            outline_color = primary if i % 2 == 0 else secondary

            self.canvas.create_oval(
                cx - r_x, cy - r_y, cx + r_x, cy + r_y,
                outline=outline_color, width=2, dash=(4, 4) if i == 1 else None
            )

        self.canvas.create_arc(
            cx - 40, cy - 30, cx + 40, cy + 30,
            start=self.angle * 1.5, extent=130, style="arc", outline=primary, width=3
        )
        self.canvas.create_arc(
            cx - 27, cy - 37, cx + 27, cy + 37,
            start=-self.angle, extent=160, style="arc", outline=secondary, width=2
        )

        pulse = math.sin(math.radians(self.angle * 6)) * (8 if self.state == "speaking" else 2)
        self.canvas.create_oval(
            cx - 4 + pulse, cy - 4 + pulse, cx + 4 + pulse, cy + 4 + pulse,
            fill=primary, outline=""
        )

        if self.show_telemetry:
            now = time.monotonic()
            if now - self._last_telemetry_update >= 1.5:
                self._last_telemetry_update = now
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory().percent
                self.canvas.create_text(
                    46, 12, text=f"CPU:{cpu}% RAM:{ram}%", fill=secondary, font=("DejaVu Sans Mono", 8, "bold")
                )

        self.animate_rings()
