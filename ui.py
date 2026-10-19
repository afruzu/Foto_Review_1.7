"""Foto_Review v1.7.0 - Interfaccia Grafica Moderna, Reattiva & Multithread.

Novità v1.7:
- Eliminazione di tutte le righe e bordi neri, sostituiti da un elegante tema blu moderno e luminoso.
- Motore di sincronizzazione ed estrazione SHA-256 / EXIF completamente MULTITHREAD su thread separato:
  nessun blocco dell'applicazione, nessun messaggio 'Non risponde' da Windows e barra di avanzamento
  che si aggiorna in tempo reale durante la scansione delle cartelle.
- Cornice sensoriale a contrasto con bordo di 4 pixel ad altissima saturazione e sfondi più saturi
  e immediatamente distinguibili (Verde vivido KEEP, Rosso vivido DISCARD, Ambra/Giallo vivido ALTRO,
  Blu intenso/Grigio PENDING) per una risposta visiva e sensoriale istantanea anche con foto a pieno schermo.
- Gestione rigorosa dei testi dinamici e dei percorsi lunghi: cancellazione esplicita delle vecchie
  stringhe prima dell'aggiornamento, wraplength calibrati, abbreviazione intelligente dei path e packing
  protetto dei pulsanti per eliminare qualsiasi sovrapposizione.
"""

import os
import json
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, Toplevel
from PIL import Image, ImageTk
from datetime import datetime

from db_analyzer import DBAnalyzer
from output import OutputManager, PRESET_QUERIES


# ==============================================================================
# TEMA COLORI & COSTANTI VISIVE v1.7 (Palette Blue & Vibrant Sensory)
# ==============================================================================
PALETTE = {
    # Layout Base
    "bg_app": "#f0f4f8",           # Sfondo principale neutro luminoso
    "bg_surface": "#ffffff",       # Superficie card e contenitori (Bianco)
    "bg_header": "#1e40af",        # Barra superiore Blu Reale (prestigioso, non tetro)
    "fg_header": "#ffffff",        # Testo header bianco
    "header_sub": "#bfdbfe",       # Sottotitoli azzurro chiaro
    
    # Bordi e Righe (Sostituiscono completamente le righe nere con tonalità blu)
    "border_blue": "#3b82f6",      # Bordo blu vivo moderno
    "border_subtle": "#93c5fd",    # Bordo / divisore azzurro chiaro morbido
    "border_card": "#bfdbfe",      # Cornice card azzurra
    
    # Tipografia
    "text_primary": "#0f172a",     # Testo primario scuro nitido
    "text_secondary": "#334155",   # Testo secondario slate
    "text_muted": "#64748b",       # Testo discreto / disabilitato
    
    # Accenti Principali
    "primary": "#2563eb",          # Blu primario per pulsanti d'azione
    "primary_hover": "#1d4ed8",    # Blu scuro hover
    "primary_light": "#eff6ff",    # Sfondo azzurro tenue
    
    # --------------------------------------------------------------------------
    # COLORI STATI DECISIONALI (Più saturi per messaggio sensoriale immediato)
    # --------------------------------------------------------------------------
    # KEEP (Conservata)
    "keep_bg": "#86efac",          # Sfondo verde vivido saturo (Emerald 300)
    "keep_border": "#15803d",      # Bordo cornice spesso verde intenso (4px)
    "keep_fg": "#052e16",          # Testo verde scuro ad alto contrasto
    "keep_badge": "#16a34a",       # Badge verde vivo
    
    # DISCARD (Scartata)
    "discard_bg": "#fca5a5",       # Sfondo rosso/corallo vivido saturo (Red 300)
    "discard_border": "#dc2626",   # Bordo cornice spesso rosso intenso (4px)
    "discard_fg": "#450a0a",       # Testo rosso scuro ad alto contrasto
    "discard_badge": "#dc2626",    # Badge rosso vivo
    
    # ALTRO (Anomalia / Materiale speciale)
    "altro_bg": "#fde047",         # Sfondo giallo/ambra vivido saturo (Yellow 300)
    "altro_border": "#d97706",     # Bordo cornice spesso ambra/oro intenso (4px)
    "altro_fg": "#451a03",         # Testo ambra scuro ad alto contrasto
    "altro_badge": "#d97706",      # Badge ambra vivo
    
    # PENDING (In attesa)
    "pending_bg": "#cbd5e1",       # Sfondo grigio/slate 300 ben distinto dal bianco
    "pending_border": "#2563eb",   # Bordo cornice blu vivo (3px)
    "pending_fg": "#0f172a",       # Testo scuro
    "pending_badge": "#3b82f6",    # Badge blu vivo
}

FONT_FAMILY = "Segoe UI"


def format_bytes(num_bytes):
    """Formatta i byte in unità umane leggibili (B, KB, MB, GB)."""
    if num_bytes is None or num_bytes < 0:
        return "0 B"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def shorten_path(path_str, max_len=65):
    """Accorcia un percorso lungo inserendo un'ellissi centrale per evitare sovrapposizioni."""
    if not path_str or len(path_str) <= max_len:
        return path_str
    half = (max_len - 5) // 2
    return path_str[:half] + " ... " + path_str[-half:]


# ==============================================================================
# APPLICAZIONE PRINCIPALE: PhotoReviewerApp v1.7
# ==============================================================================
class PhotoReviewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Foto Review v1.7 - Professional Edition")
        self.root.geometry("1260x830")
        self.root.minsize(1040, 680)
        self.root.configure(bg=PALETTE["bg_app"])

        # Stato interno
        self.folder_path = ""
        self.db_path = ""
        self.db_analyzer = None
        self.output_manager = None
        self.image_files = []
        self.current_index = 0
        self.current_image_raw = None
        self.photo_tk = None
        self.rotation_angle = 0
        self.session_file = ""
        self.is_corrupt = False
        self.is_scanning = False
        self.scan_queue = queue.Queue()

        # Undo a singolo step: (filename, old_decision, index)
        self.last_action = None

        # Debounce per il ridimensionamento della canvas
        self._resize_timer = None
        self._last_w = 0
        self._last_h = 0

        self.setup_styles()
        self.create_widgets()
        self.bind_shortcuts()

    def setup_styles(self):
        """Configura gli stili ttk per un look moderno con accenti blu."""
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            "TProgressbar",
            thickness=10,
            troughcolor="#dbeafe",
            background=PALETTE["primary"],
            relief="flat",
        )
        style.configure(
            "Card.TFrame",
            background=PALETTE["bg_surface"],
            relief="flat",
        )

    # --------------------------------------------------------------------------
    # COSTRUZIONE INTERFACCIA GRAFICA
    # --------------------------------------------------------------------------
    def create_widgets(self):
        # 1. Header Bar Superiore Blu
        self.create_header_bar()

        # 2. Dashboard Sotto-Header (Metriche Live & Progresso)
        self.create_dashboard_bar()

        # 3. Area Centrale (Viewer Immagine + Sidebar Metadati)
        self.create_center_container()

        # 4. Barra di Controllo & Azioni Inferiore
        self.create_bottom_controls()

        # 5. Footer Stato Applicazione
        self.create_footer_status()

    def create_header_bar(self):
        """Barra superiore moderna in Blu Reale luminoso, priva di elementi tetri o neri."""
        header = tk.Frame(self.root, bg=PALETTE["bg_header"], height=58, padx=16, pady=8)
        header.pack(side=tk.TOP, fill=tk.X)

        # Brand / Titolo
        brand_frame = tk.Frame(header, bg=PALETTE["bg_header"])
        brand_frame.pack(side=tk.LEFT)

        lbl_logo = tk.Label(
            brand_frame,
            text="📸 Foto Review 1.7",
            font=(FONT_FAMILY, 13, "bold"),
            fg=PALETTE["fg_header"],
            bg=PALETTE["bg_header"],
        )
        lbl_logo.pack(side=tk.LEFT, padx=(0, 10))

        lbl_desc = tk.Label(
            brand_frame,
            text="|  Revisione Fotografica, Deduplicazione & Archiviazione Multithread",
            font=(FONT_FAMILY, 9),
            fg=PALETTE["header_sub"],
            bg=PALETTE["bg_header"],
        )
        lbl_desc.pack(side=tk.LEFT)

        # Pulsanti Destra Header
        btn_box = tk.Frame(header, bg=PALETTE["bg_header"])
        btn_box.pack(side=tk.RIGHT)

        self.btn_select_folder = tk.Button(
            btn_box,
            text="📁 Seleziona Cartella Foto",
            command=self.select_folder,
            font=(FONT_FAMILY, 9, "bold"),
            bg="#2563eb",
            fg="white",
            activebackground="#1d4ed8",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.btn_select_folder.pack(side=tk.LEFT, padx=4)

        self.btn_output_top = tk.Button(
            btn_box,
            text="📊 Centro Output...",
            command=self.open_output_panel,
            font=(FONT_FAMILY, 9, "bold"),
            bg="#1d4ed8",
            fg="white",
            activebackground="#1e40af",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.btn_output_top.pack(side=tk.LEFT, padx=4)

        btn_shortcuts = tk.Button(
            btn_box,
            text="⌨️ Tasti Rapidi [F1]",
            command=self.show_shortcuts_help,
            font=(FONT_FAMILY, 9),
            bg="#1e3a8a",
            fg="#bfdbfe",
            activebackground="#1d4ed8",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
        )
        btn_shortcuts.pack(side=tk.LEFT, padx=4)

        btn_exit = tk.Button(
            btn_box,
            text="✕ Esci",
            command=self.exit_app,
            font=(FONT_FAMILY, 9),
            bg="#b91c1c",
            fg="#fee2e2",
            activebackground="#991b1b",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
        )
        btn_exit.pack(side=tk.LEFT, padx=(4, 0))

    def create_dashboard_bar(self):
        """Dashboard metrica con bordo blu luminoso (nessuna riga nera)."""
        dash = tk.Frame(
            self.root,
            bg=PALETTE["bg_surface"],
            highlightbackground=PALETTE["border_blue"],
            highlightcolor=PALETTE["border_blue"],
            highlightthickness=1,
            padx=16,
            pady=8,
        )
        dash.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(10, 5))

        # Riga Superiore: Percorso Cartella & Metriche
        top_dash = tk.Frame(dash, bg=PALETTE["bg_surface"])
        top_dash.pack(fill=tk.X)

        self.lbl_folder_display = tk.Label(
            top_dash,
            text="Nessuna cartella selezionata — Seleziona una cartella per iniziare",
            font=(FONT_FAMILY, 10, "bold"),
            fg=PALETTE["primary"],
            bg=PALETTE["bg_surface"],
            anchor=tk.W,
            justify=tk.LEFT,
        )
        self.lbl_folder_display.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Chips contatori sulla destra con bordi colorati morbidi (no nero)
        self.chips_frame = tk.Frame(top_dash, bg=PALETTE["bg_surface"])
        self.chips_frame.pack(side=tk.RIGHT)

        self.lbl_chip_keep = self.create_chip(self.chips_frame, "✓ KEEP: 0", "#dcfce7", "#15803d", "#86efac")
        self.lbl_chip_discard = self.create_chip(self.chips_frame, "✕ DISCARD: 0", "#fee2e2", "#991b1b", "#fca5a5")
        self.lbl_chip_altro = self.create_chip(self.chips_frame, "⚠ ALTRO: 0", "#fef3c7", "#92400e", "#fde047")
        self.lbl_chip_pending = self.create_chip(self.chips_frame, "⏳ PENDING: 0", "#eff6ff", "#1e40af", "#93c5fd")

        # Riga Inferiore: Progresso, Indice e Salto Rapido
        bot_dash = tk.Frame(dash, bg=PALETTE["bg_surface"], pady=4)
        bot_dash.pack(fill=tk.X, pady=(6, 0))

        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(
            bot_dash,
            variable=self.progress_var,
            maximum=100.0,
            style="TProgressbar",
            length=260,
        )
        self.progress_bar.pack(side=tk.LEFT, padx=(0, 10))

        self.lbl_progress_text = tk.Label(
            bot_dash,
            text="0 / 0 foto (0.0% completato)",
            font=(FONT_FAMILY, 9),
            fg=PALETTE["text_secondary"],
            bg=PALETTE["bg_surface"],
        )
        self.lbl_progress_text.pack(side=tk.LEFT)

        # Salto Rapido
        jump_box = tk.Frame(bot_dash, bg=PALETTE["bg_surface"])
        jump_box.pack(side=tk.RIGHT)

        tk.Label(jump_box, text="Vai a foto #", font=(FONT_FAMILY, 9), fg=PALETTE["text_secondary"], bg=PALETTE["bg_surface"]).pack(side=tk.LEFT, padx=3)
        self.entry_jump = tk.Entry(
            jump_box,
            font=(FONT_FAMILY, 9),
            width=6,
            justify=tk.CENTER,
            highlightthickness=1,
            highlightbackground=PALETTE["border_blue"],
            relief=tk.FLAT,
        )
        self.entry_jump.pack(side=tk.LEFT, padx=3)
        self.entry_jump.bind("<Return>", lambda e: self.do_jump())

        btn_jump = tk.Button(
            jump_box,
            text="Vai",
            command=self.do_jump,
            font=(FONT_FAMILY, 8, "bold"),
            bg="#dbeafe",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=1,
            cursor="hand2",
        )
        btn_jump.pack(side=tk.LEFT, padx=2)

    def create_chip(self, parent, text, bg_col, fg_col, border_col):
        chip = tk.Label(
            parent,
            text=text,
            font=(FONT_FAMILY, 8, "bold"),
            bg=bg_col,
            fg=fg_col,
            highlightthickness=1,
            highlightbackground=border_col,
            padx=8,
            pady=2,
        )
        chip.pack(side=tk.LEFT, padx=3)
        return chip

    def create_center_container(self):
        container = tk.Frame(self.root, bg=PALETTE["bg_app"])
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=5)

        # ----------------------------------------------------------------------
        # AREA SINISTRA: VIEWER IMMAGINE CON CORNICE SENSORIALE A 4 PIXEL (Point 3)
        # ----------------------------------------------------------------------
        self.viewer_card = tk.Frame(
            container,
            bg=PALETTE["pending_bg"],
            highlightbackground=PALETTE["pending_border"],
            highlightcolor=PALETTE["pending_border"],
            highlightthickness=4,  # Bordo spesso saturo da 4 pixel richiesto dal punto 3
        )
        self.viewer_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        # Top Bar Viewer: Badge di Stato e Controlli Rotazione
        viewer_top = tk.Frame(self.viewer_card, bg=PALETTE["bg_surface"], padx=10, pady=8)
        viewer_top.pack(fill=tk.X)

        self.badge_status = tk.Label(
            viewer_top,
            text="⏳  IN ATTESA DI REVISIONE (PENDING)",
            font=(FONT_FAMILY, 10, "bold"),
            bg=PALETTE["pending_badge"],
            fg="white",
            padx=14,
            pady=4,
        )
        self.badge_status.pack(side=tk.LEFT)

        self.lbl_decision_timestamp = tk.Label(
            viewer_top,
            text="",
            font=(FONT_FAMILY, 8, "italic"),
            fg=PALETTE["text_muted"],
            bg=PALETTE["bg_surface"],
        )
        self.lbl_decision_timestamp.pack(side=tk.LEFT, padx=10)

        # Controlli Rotazione a destra
        rot_box = tk.Frame(viewer_top, bg=PALETTE["bg_surface"])
        rot_box.pack(side=tk.RIGHT)

        btn_rot_left = tk.Button(
            rot_box,
            text="↺ -90° (Sx)",
            command=lambda: self.rotate_image(90),
            font=(FONT_FAMILY, 8),
            bg="#eff6ff",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
        )
        btn_rot_left.pack(side=tk.LEFT, padx=2)

        btn_rot_right = tk.Button(
            rot_box,
            text="↻ +90° (Dx)",
            command=lambda: self.rotate_image(-90),
            font=(FONT_FAMILY, 8),
            bg="#eff6ff",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
        )
        btn_rot_right.pack(side=tk.LEFT, padx=2)

        self.lbl_rotation_val = tk.Label(
            rot_box,
            text="0°",
            font=(FONT_FAMILY, 8, "bold"),
            fg=PALETTE["text_secondary"],
            bg=PALETTE["bg_surface"],
            width=5,
        )
        self.lbl_rotation_val.pack(side=tk.LEFT, padx=2)

        # Canvas dell'immagine con bordo interno sensoriale da 2 pixel
        self.canvas_bg = PALETTE["pending_bg"]
        self.canvas = tk.Canvas(
            self.viewer_card,
            bg=self.canvas_bg,
            highlightthickness=2,
            highlightbackground=PALETTE["pending_border"],
            bd=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.canvas.bind("<Configure>", self.on_canvas_resize)

        # ----------------------------------------------------------------------
        # AREA DESTRA: SIDEBAR METADATI & DETTAGLI STRUTTURATI (No righe nere)
        # ----------------------------------------------------------------------
        sidebar = tk.Frame(
            container,
            width=360,
            bg=PALETTE["bg_surface"],
            highlightbackground=PALETTE["border_blue"],
            highlightcolor=PALETTE["border_blue"],
            highlightthickness=1,
        )
        sidebar.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        sidebar.pack_propagate(False)

        # Header Sidebar in Blu Reale
        sb_header = tk.Frame(sidebar, bg=PALETTE["bg_header"], padx=12, pady=10)
        sb_header.pack(fill=tk.X)
        tk.Label(
            sb_header,
            text="📋 Scheda Metadati & Dettagli",
            font=(FONT_FAMILY, 10, "bold"),
            fg="white",
            bg=PALETTE["bg_header"],
        ).pack(side=tk.LEFT)

        # Contenitore sezioni
        sb_content = tk.Frame(sidebar, bg=PALETTE["bg_surface"], padx=10, pady=8)
        sb_content.pack(fill=tk.BOTH, expand=True)

        # Sezione 1: Proprietà File
        self.card_file = self.create_section_card(sb_content, "Proprietà File")
        self.lbl_filename_val = self.create_key_value(self.card_file, "Nome File:", "—", copyable=True)
        self.lbl_size_val = self.create_key_value(self.card_file, "Dimensione:", "—")
        self.lbl_res_val = self.create_key_value(self.card_file, "Risoluzione:", "—")
        self.lbl_sha_val = self.create_key_value(self.card_file, "SHA-256:", "—", copyable=True)

        # Sezione 2: Cronologia
        self.card_chrono = self.create_section_card(sb_content, "Cronologia & Origine Data")
        self.lbl_date_val = self.create_key_value(self.card_chrono, "Data Calcolata:", "—", bold_val=True)
        self.lbl_origin_val = self.create_key_value(self.card_chrono, "Origine Data:", "—")
        self.lbl_state_val = self.create_key_value(self.card_chrono, "Stato Attuale:", "—")

        # Sezione 3: Parametri di Scatto
        self.card_camera = self.create_section_card(sb_content, "Fotocamera & Scatto")
        self.lbl_camera_val = self.create_key_value(self.card_camera, "Dispositivo:", "—")
        self.lbl_shot_val = self.create_key_value(self.card_camera, "Parametri:", "—")

        # Sezione 4: Tutti i Metadati EXIF (Box scorrevole)
        card_exif = self.create_section_card(sb_content, "Tutti i Metadati EXIF")
        self.txt_exif = tk.Text(
            card_exif,
            wrap=tk.WORD,
            font=("Consolas", 8),
            height=7,
            bg="#f8fafc",
            fg=PALETTE["text_primary"],
            highlightthickness=1,
            highlightbackground=PALETTE["border_subtle"],
            bd=0,
            state=tk.DISABLED,
        )
        self.txt_exif.pack(fill=tk.BOTH, expand=True, pady=3)

    def create_section_card(self, parent, title):
        """Card sezione con linea di separazione azzurra (no nero)."""
        frame = tk.Frame(parent, bg=PALETTE["bg_surface"], pady=3)
        frame.pack(fill=tk.X, pady=2)
        lbl = tk.Label(
            frame,
            text=title.upper(),
            font=(FONT_FAMILY, 8, "bold"),
            fg=PALETTE["primary"],
            bg=PALETTE["bg_surface"],
        )
        lbl.pack(anchor=tk.W, pady=(0, 2))
        # Divisore blu chiaro morbido
        div = tk.Frame(frame, bg=PALETTE["border_subtle"], height=1)
        div.pack(fill=tk.X, pady=(0, 4))
        return frame

    def create_key_value(self, parent, key, val, bold_val=False, copyable=False):
        """Genera riga chiave-valore con protezione contro sovrapposizioni di testo (Point 4)."""
        row = tk.Frame(parent, bg=PALETTE["bg_surface"])
        row.pack(fill=tk.X, pady=1)

        tk.Label(
            row,
            text=key,
            font=(FONT_FAMILY, 8),
            fg=PALETTE["text_secondary"],
            bg=PALETTE["bg_surface"],
            width=13,
            anchor=tk.W,
        ).pack(side=tk.LEFT)

        # Se copiabile, il pulsante viene posizionato a DESTRA prima del label per non essere mai spinto via
        if copyable:
            btn_copy = tk.Button(
                row,
                text="📋",
                font=(FONT_FAMILY, 7),
                bg="#eff6ff",
                fg=PALETTE["primary"],
                relief=tk.FLAT,
                bd=0,
                padx=4,
                pady=0,
                cursor="hand2",
                command=lambda: self.copy_to_clipboard(lbl_v.cget("text")),
            )
            btn_copy.pack(side=tk.RIGHT, padx=(2, 0))

        f_val = (FONT_FAMILY, 8, "bold" if bold_val else "normal")
        lbl_v = tk.Label(
            row,
            text=val,
            font=f_val,
            fg=PALETTE["text_primary"],
            bg=PALETTE["bg_surface"],
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=210,  # Previene overflow orizzontale
        )
        lbl_v.pack(side=tk.LEFT, fill=tk.X, expand=True)

        return lbl_v

    def create_bottom_controls(self):
        """Barra inferiore con bordi azzurri e pulsanti moderni ergonomici."""
        bottom = tk.Frame(
            self.root,
            bg=PALETTE["bg_surface"],
            highlightbackground=PALETTE["border_blue"],
            highlightcolor=PALETTE["border_blue"],
            highlightthickness=1,
            padx=16,
            pady=10,
        )
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=(5, 5))

        # Navigazione Cronologica
        nav_frame = tk.Frame(bottom, bg=PALETTE["bg_surface"])
        nav_frame.pack(side=tk.LEFT)

        btn_first = tk.Button(
            nav_frame,
            text="|◀ Inizio",
            command=lambda: self.navigate_to_index(0),
            font=(FONT_FAMILY, 9),
            bg="#f1f5f9",
            fg=PALETTE["text_primary"],
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=7,
            cursor="hand2",
        )
        btn_first.pack(side=tk.LEFT, padx=2)

        btn_p10 = tk.Button(
            nav_frame,
            text="◀◀ -10",
            command=lambda: self.navigate_relative(-10),
            font=(FONT_FAMILY, 9),
            bg="#f1f5f9",
            fg=PALETTE["text_primary"],
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=7,
            cursor="hand2",
        )
        btn_p10.pack(side=tk.LEFT, padx=2)

        self.btn_prev = tk.Button(
            nav_frame,
            text="▲ Prec [↑]",
            command=lambda: self.navigate_relative(-1),
            font=(FONT_FAMILY, 9, "bold"),
            bg="#dbeafe",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
        )
        self.btn_prev.pack(side=tk.LEFT, padx=3)

        self.btn_next = tk.Button(
            nav_frame,
            text="Succ [↓] ▼",
            command=lambda: self.navigate_relative(1),
            font=(FONT_FAMILY, 9, "bold"),
            bg="#dbeafe",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
        )
        self.btn_next.pack(side=tk.LEFT, padx=3)

        btn_n10 = tk.Button(
            nav_frame,
            text="+10 ▶▶",
            command=lambda: self.navigate_relative(10),
            font=(FONT_FAMILY, 9),
            bg="#f1f5f9",
            fg=PALETTE["text_primary"],
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=7,
            cursor="hand2",
        )
        btn_n10.pack(side=tk.LEFT, padx=2)

        btn_last = tk.Button(
            nav_frame,
            text="Fine ▶|",
            command=lambda: self.navigate_to_index(len(self.image_files) - 1),
            font=(FONT_FAMILY, 9),
            bg="#f1f5f9",
            fg=PALETTE["text_primary"],
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=7,
            cursor="hand2",
        )
        btn_last.pack(side=tk.LEFT, padx=2)

        # Pulsanti Decisione Principali
        dec_frame = tk.Frame(bottom, bg=PALETTE["bg_surface"])
        dec_frame.pack(side=tk.RIGHT)

        # Scarta [N]
        self.btn_discard = tk.Button(
            dec_frame,
            text="✕  SCARTA [N]",
            command=lambda: self.save_decision('DISCARD'),
            font=(FONT_FAMILY, 10, "bold"),
            bg="#fee2e2",
            fg="#991b1b",
            activebackground="#fca5a5",
            activeforeground="#7f1d1d",
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
        )
        self.btn_discard.pack(side=tk.LEFT, padx=4)

        # Altro [A]
        self.btn_altro = tk.Button(
            dec_frame,
            text="⚠  ALTRO [A]",
            command=lambda: self.save_decision('ALTRO'),
            font=(FONT_FAMILY, 10, "bold"),
            bg="#fef3c7",
            fg="#92400e",
            activebackground="#fde047",
            activeforeground="#78350f",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
        )
        self.btn_altro.pack(side=tk.LEFT, padx=4)

        # Salta [Spazio]
        self.btn_skip = tk.Button(
            dec_frame,
            text="⏭  SALTA [Spazio]",
            command=self.skip_photo,
            font=(FONT_FAMILY, 10),
            bg="#f1f5f9",
            fg="#334155",
            activebackground="#e2e8f0",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
        )
        self.btn_skip.pack(side=tk.LEFT, padx=4)

        # Conserva [S]
        self.btn_keep = tk.Button(
            dec_frame,
            text="✓  CONSERVA [S]",
            command=lambda: self.save_decision('KEEP'),
            font=(FONT_FAMILY, 10, "bold"),
            bg="#dcfce7",
            fg="#15803d",
            activebackground="#86efac",
            activeforeground="#14532d",
            relief=tk.FLAT,
            bd=0,
            padx=16,
            pady=7,
            cursor="hand2",
        )
        self.btn_keep.pack(side=tk.LEFT, padx=4)

        # Separatore azzurro
        tk.Frame(dec_frame, bg=PALETTE["border_subtle"], width=1, height=26).pack(side=tk.LEFT, padx=8)

        # Annulla [Ctrl+Z]
        self.btn_undo = tk.Button(
            dec_frame,
            text="↩ Annulla [Ctrl+Z]",
            command=self.undo_last_action,
            font=(FONT_FAMILY, 9),
            bg="#fff7ed",
            fg="#c2410c",
            activebackground="#ffedd5",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=7,
            cursor="hand2",
        )
        self.btn_undo.pack(side=tk.LEFT, padx=4)

    def create_footer_status(self):
        footer = tk.Frame(self.root, bg=PALETTE["bg_app"], padx=16, pady=4)
        footer.pack(side=tk.BOTTOM, fill=tk.X)

        self.lbl_footer_status = tk.Label(
            footer,
            text="Pronto. Seleziona una cartella fotografica per iniziare.",
            font=(FONT_FAMILY, 8),
            fg=PALETTE["text_secondary"],
            bg=PALETTE["bg_app"],
            anchor=tk.W,
        )
        self.lbl_footer_status.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.lbl_footer_db = tk.Label(
            footer,
            text="Database: Non connesso",
            font=(FONT_FAMILY, 8, "italic"),
            fg=PALETTE["primary"],
            bg=PALETTE["bg_app"],
        )
        self.lbl_footer_db.pack(side=tk.RIGHT)

    # --------------------------------------------------------------------------
    # SCORCIATOIE DA TASTIERA
    # --------------------------------------------------------------------------
    def bind_shortcuts(self):
        self.root.bind("s", lambda e: self.save_decision('KEEP'))
        self.root.bind("S", lambda e: self.save_decision('KEEP'))
        self.root.bind("n", lambda e: self.save_decision('DISCARD'))
        self.root.bind("N", lambda e: self.save_decision('DISCARD'))
        self.root.bind("a", lambda e: self.save_decision('ALTRO'))
        self.root.bind("A", lambda e: self.save_decision('ALTRO'))
        self.root.bind("<space>", lambda e: self.skip_photo())
        self.root.bind("<Control-z>", lambda e: self.undo_last_action())
        self.root.bind("<Control-Z>", lambda e: self.undo_last_action())

        # Navigazione
        self.root.bind("<Up>", lambda e: self.navigate_relative(-1))
        self.root.bind("<Down>", lambda e: self.navigate_relative(1))
        self.root.bind("<Control-Up>", lambda e: self.navigate_relative(-10))
        self.root.bind("<Control-Down>", lambda e: self.navigate_relative(10))
        self.root.bind("<Prior>", lambda e: self.navigate_relative(-100))
        self.root.bind("<Next>", lambda e: self.navigate_relative(100))
        self.root.bind("<Home>", lambda e: self.navigate_to_index(0))
        self.root.bind("<End>", lambda e: self.navigate_to_index(len(self.image_files) - 1))

        # Rotazione
        self.root.bind("<Left>", lambda e: self.rotate_image(90))
        self.root.bind("<Right>", lambda e: self.rotate_image(-90))

        # Aiuto e Output
        self.root.bind("<F1>", lambda e: self.show_shortcuts_help())
        self.root.bind("<Control-o>", lambda e: self.open_output_panel())
        self.root.bind("<Control-O>", lambda e: self.open_output_panel())

    # --------------------------------------------------------------------------
    # CARICAMENTO ASINCRONO MULTITHREAD & PROGRESS OVERLAY (Point 2 & Point 4)
    # --------------------------------------------------------------------------
    def select_folder(self):
        if self.is_scanning:
            return
        folder = filedialog.askdirectory(title="Seleziona la cartella contenente le foto da revisionare")
        if not folder:
            return
        self.load_folder_session(folder)

    def load_folder_session(self, folder):
        """Avvia la sessione con scansione asincrona multithread senza bloccare la UI."""
        self.folder_path = folder
        if self.db_analyzer:
            self.db_analyzer.close()

        self.db_path = os.path.join(self.folder_path, "photo_reviews.db")
        self.session_file = os.path.join(self.folder_path, ".photo_review_session.json")

        self.db_analyzer = DBAnalyzer(self.folder_path)
        self.output_manager = OutputManager(self.folder_path, self.db_path)

        # Cancellazione preventiva vecchie stringhe per evitare sovrapposizioni (Point 4)
        self.lbl_folder_display.config(text="")
        display_path = shorten_path(self.folder_path, max_len=60)
        self.lbl_folder_display.config(
            text=f"📁 Cartella: {os.path.basename(self.folder_path)}  ({display_path})"
        )
        self.lbl_footer_db.config(text=f"● Connesso: {os.path.basename(self.db_path)}")
        self.lbl_footer_status.config(text="Ricerca file fotografici in corso...")

        try:
            all_files = os.listdir(self.folder_path)
            valid_files = [
                f for f in all_files
                if f.lower().endswith(self.db_analyzer.valid_extensions) and os.path.isfile(os.path.join(self.folder_path, f))
            ]
        except Exception as e:
            messagebox.showerror("Errore lettura cartella", str(e))
            return

        if not valid_files:
            messagebox.showwarning(
                "Nessuna foto trovata",
                "Non sono state trovate immagini valide (.jpg, .jpeg, .png, .bmp, .webp, .heic) nella cartella selezionata."
            )
            return

        # Svuota eventuali messaggi residui nella coda di scansione
        while not self.scan_queue.empty():
            try:
                self.scan_queue.get_nowait()
            except queue.Empty:
                break

        # Crea e mostra la finestra modale di progresso non-bloccante
        self.show_loading_modal(len(valid_files))

        # Avvio del thread worker di scansione
        self.is_scanning = True
        self.btn_select_folder.config(state=tk.DISABLED)

        t = threading.Thread(
            target=self._async_scan_worker,
            args=(valid_files,),
            daemon=True
        )
        t.start()

        # Avvia il polling periodico della coda sul thread principale di Tkinter
        self.root.after(30, self._poll_scan_queue)

    def show_loading_modal(self, total_files):
        """Finestra modale di avanzamento live multithread (Point 2)."""
        dlg = Toplevel(self.root)
        dlg.title("Scansione Multithread Archivio - Foto Review 1.7")
        dlg.geometry("520x220")
        dlg.resizable(False, False)
        dlg.configure(bg=PALETTE["bg_surface"])
        dlg.transient(self.root)
        dlg.grab_set()

        # Posiziona al centro della finestra principale
        try:
            x = self.root.winfo_x() + (self.root.winfo_width() - 520) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - 220) // 2
            dlg.geometry(f"+{x}+{y}")
        except Exception:
            pass

        dlg_header = tk.Frame(dlg, bg=PALETTE["bg_header"], padx=14, pady=10)
        dlg_header.pack(fill=tk.X)

        tk.Label(
            dlg_header,
            text="⚡ Elaborazione Multithread in Corso",
            font=(FONT_FAMILY, 11, "bold"),
            fg="white",
            bg=PALETTE["bg_header"],
        ).pack(anchor=tk.W)

        num_cores = os.cpu_count() or 4
        tk.Label(
            dlg_header,
            text=f"Calcolo checksum SHA-256 ed estrazione metadati su {num_cores} core CPU paralleli",
            font=(FONT_FAMILY, 8),
            fg="#bfdbfe",
            bg=PALETTE["bg_header"],
        ).pack(anchor=tk.W)

        body = tk.Frame(dlg, bg=PALETTE["bg_surface"], padx=16, pady=12)
        body.pack(fill=tk.BOTH, expand=True)

        self._loading_lbl_info = tk.Label(
            body,
            text=f"Analisi di {total_files} file fotografici in corso...",
            font=(FONT_FAMILY, 9, "bold"),
            fg=PALETTE["text_primary"],
            bg=PALETTE["bg_surface"],
            anchor=tk.W,
        )
        self._loading_lbl_info.pack(fill=tk.X, pady=(2, 6))

        self._loading_progress_var = tk.DoubleVar(value=0.0)
        self._loading_pbar = ttk.Progressbar(
            body,
            variable=self._loading_progress_var,
            maximum=100.0,
            style="TProgressbar",
        )
        self._loading_pbar.pack(fill=tk.X, pady=6)

        self._loading_lbl_detail = tk.Label(
            body,
            text="Avvio worker pool multithread...",
            font=(FONT_FAMILY, 8),
            fg=PALETTE["text_secondary"],
            bg=PALETTE["bg_surface"],
            anchor=tk.W,
            wraplength=480,
        )
        self._loading_lbl_detail.pack(fill=tk.X, pady=2)

        self._loading_dlg = dlg
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)

    def _async_scan_worker(self, valid_files):
        """Esecuzione su thread secondario: calcolo SHA-256, EXIF e deduplicazione."""
        total_files = len(valid_files)

        def progress_cb(cur, tot, filename):
            # Invia l'aggiornamento alla coda thread-safe (senza toccare Tkinter)
            self.scan_queue.put(("progress", cur, tot, filename))

        try:
            self.db_analyzer.sync_files_to_db_multithread(valid_files, progress_callback=progress_cb)

            # Deduplicazione automatica v1.5 / v1.6 / v1.7
            try:
                n_dup, dup_errors = self.db_analyzer.deduplicate_exact_copies("duplicati certi")
            except Exception as e:
                print(f"[Deduplicazione warning]: {e}")
                n_dup, dup_errors = 0, []

            sorted_files = self.db_analyzer.get_sorted_files()
            resumption_idx = self.get_resumption_index_from_list(sorted_files)

            # Segnala il completamento alla coda thread-safe
            self.scan_queue.put(("done", sorted_files, resumption_idx, n_dup))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.scan_queue.put(("error", str(e)))

    def _poll_scan_queue(self):
        """Polling eseguito esclusivamente sul thread principale di Tkinter."""
        if not self.is_scanning:
            return

        try:
            while True:
                msg = self.scan_queue.get_nowait()
                msg_type = msg[0]
                if msg_type == "progress":
                    _, cur, tot, filename = msg
                    self._update_scan_progress_ui(cur, tot, filename)
                elif msg_type == "done":
                    _, sorted_files, resumption_idx, n_dup = msg
                    self._finalize_scan_ui(sorted_files, resumption_idx, n_dup)
                    return
                elif msg_type == "error":
                    _, err_msg = msg
                    self._handle_scan_error_ui(err_msg)
                    return
        except queue.Empty:
            pass

        if self.is_scanning:
            self.root.after(30, self._poll_scan_queue)

    def _handle_scan_error_ui(self, err_msg):
        """Gestisce in modo pulito eventuali errori durante la scansione background."""
        self.is_scanning = False
        self.btn_select_folder.config(state=tk.NORMAL)
        try:
            if hasattr(self, "_loading_dlg") and self._loading_dlg:
                self._loading_dlg.destroy()
        except Exception:
            pass
        self.lbl_footer_status.config(text=f"Errore scansione: {err_msg}")
        messagebox.showerror(
            "Errore durante la scansione",
            f"Si è verificato un errore durante l'analisi della cartella:\n\n{err_msg}"
        )

    def _update_scan_progress_ui(self, cur, tot, filename):
        """Aggiorna la modale di caricamento (chiamato sul thread principale)."""
        pct = (cur / tot * 100.0) if tot > 0 else 0.0
        try:
            self._loading_progress_var.set(pct)
            # Cancellazione preventiva per evitare sovrapposizioni
            self._loading_lbl_info.config(text="")
            self._loading_lbl_detail.config(text="")

            self._loading_lbl_info.config(
                text=f"Analisi file: {cur} di {tot}  ({pct:.1f}% completato)"
            )
            self._loading_lbl_detail.config(
                text=f"File: {filename}"
            )
        except Exception:
            pass

    def _finalize_scan_ui(self, sorted_files, resumption_idx, n_dup):
        """Chiamato al termine della scansione: distrugge la modale e carica le foto."""
        self.is_scanning = False
        self.btn_select_folder.config(state=tk.NORMAL)

        try:
            if hasattr(self, "_loading_dlg") and self._loading_dlg:
                self._loading_dlg.destroy()
        except Exception:
            pass

        self.image_files = sorted_files
        self.current_index = resumption_idx

        # Notifica visiva trasparente per duplicati certi
        if n_dup > 0:
            messagebox.showinfo(
                "Deduplicazione Automatica v1.7",
                f"Rilevati e isolati {n_dup} file duplicati identici (stesso hash SHA-256 e dimensione).\n\n"
                f"I file duplicati sono stati spostati automaticamente nella sottocartella:\n'duplicati certi/'\n\n"
                f"Un solo originale per gruppo è rimasto nella radice di lavoro per la revisione.\n"
                f"Tutti i dati sono archiviati nella tabella 'duplicate_files'."
            )

        if self.image_files:
            self.load_current_image()
            self.lbl_footer_status.config(
                text=f"Archivio pronto: {len(self.image_files)} foto caricate con successo."
            )
        else:
            self.update_dashboard_counts()
            messagebox.showinfo(
                "Nessun file residuo",
                "Tutti i file della cartella sono stati già elaborati o spostati."
            )

    def get_resumption_index_from_list(self, files_list):
        if not files_list:
            return 0
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    last_file = data.get("last_filename")
                    if last_file in files_list:
                        return files_list.index(last_file)
            except Exception:
                pass

        for idx, filename in enumerate(files_list):
            row = self.db_analyzer.get_file_record(filename)
            if row and row[0] == 'PENDING':
                return idx
        return 0

    def save_session_state(self):
        """Salva il punto di ripresa nel file JSON di servizio."""
        if self.image_files and 0 <= self.current_index < len(self.image_files):
            current_file = self.image_files[self.current_index]
            data = {"last_filename": current_file}
            try:
                with open(self.session_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # RENDERING IMMAGINE & RESPONSIVITÀ CANVAS
    # --------------------------------------------------------------------------
    def on_canvas_resize(self, event):
        """Gestisce il ridimensionamento della finestra con debouncing."""
        if not self.current_image_raw:
            return
        if abs(event.width - self._last_w) < 15 and abs(event.height - self._last_h) < 15:
            return
        self._last_w = event.width
        self._last_h = event.height
        if self._resize_timer:
            self.root.after_cancel(self._resize_timer)
        self._resize_timer = self.root.after(70, self.update_image_display)

    def update_image_display(self):
        """Ridisegna l'immagine sulla canvas cancellando preventivamente qualsiasi vecchio elemento."""
        # Cancellazione totale preventiva per evitare sovrapposizioni su canvas (Point 4)
        self.canvas.delete("all")
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())

        if self.is_corrupt or not self.current_image_raw:
            if self.is_corrupt:
                self.canvas.create_text(
                    cw // 2, ch // 2 - 25,
                    text="⚠️ IMMAGINE NON LEGGIBILE O CORROTTA",
                    font=(FONT_FAMILY, 12, "bold"),
                    fill="#dc2626",
                    justify=tk.CENTER,
                )
                self.canvas.create_text(
                    cw // 2, ch // 2 + 15,
                    text="Il file è stato classificato automaticamente come ALTRO.\nÈ possibile isolarlo tramite il Centro Output.",
                    font=(FONT_FAMILY, 10),
                    fill=PALETTE["text_secondary"],
                    justify=tk.CENTER,
                )
            elif not self.image_files:
                self.canvas.create_text(
                    cw // 2, ch // 2,
                    text="Nessuna foto da revisionare.\nSeleziona una cartella per iniziare.",
                    font=(FONT_FAMILY, 11),
                    fill=PALETTE["text_muted"],
                    justify=tk.CENTER,
                )
            return

        try:
            img = self.current_image_raw.copy()
            if self.rotation_angle != 0:
                img = img.rotate(self.rotation_angle, expand=True)

            target_w = max(60, cw - 28)
            target_h = max(60, ch - 28)
            img.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)

            self.photo_tk = ImageTk.PhotoImage(img)
            self.canvas.create_image(cw // 2, ch // 2, anchor=tk.CENTER, image=self.photo_tk)
        except Exception:
            self.canvas.create_text(
                cw // 2, ch // 2,
                text="Errore nel rendering dell'anteprima.",
                font=(FONT_FAMILY, 10),
                fill="#dc2626",
            )

    def rotate_image(self, angle):
        """Ruota temporaneamente l'anteprima a schermo."""
        if not self.image_files or not self.current_image_raw:
            return
        self.rotation_angle = (self.rotation_angle + angle) % 360
        self.lbl_rotation_val.config(text=f"{self.rotation_angle}°")
        self.update_image_display()

    # --------------------------------------------------------------------------
    # CARICAMENTO FOTO CORRENTE & AGGIORNAMENTO SENSORIALE (Point 3 & Point 4)
    # --------------------------------------------------------------------------
    def load_current_image(self):
        self.rotation_angle = 0
        self.lbl_rotation_val.config(text="0°")

        if not self.image_files or self.current_index < 0 or self.current_index >= len(self.image_files):
            self.show_completed_state()
            return

        filename = self.image_files[self.current_index]
        filepath = os.path.join(self.folder_path, filename)

        row = self.db_analyzer.get_file_record(filename)
        decision = row[0] if row else 'PENDING'
        exif_json = row[1] if row and len(row) > 1 else "{}"
        effective_ts = row[2] if row and len(row) > 2 else "Sconosciuta"

        sha_v, size_v = self.db_analyzer.get_sha_size(filename)

        # Aggiorna Cornice Sensoriale e Sfondi Saturi (Point 3)
        self.update_sensory_frame(decision, filename)

        # Aggiorna Metadati nella Sidebar (con cancellazione preventiva per evitare sovrapposizioni)
        self.update_metadata_sidebar(filename, filepath, effective_ts, exif_json, sha_v, size_v, decision)

        # Aggiorna Dashboard e Sessione
        self.update_dashboard_counts()
        self.save_session_state()

        # Carica immagine con PIL
        try:
            self.current_image_raw = Image.open(filepath)
            self.is_corrupt = False
        except Exception:
            self.current_image_raw = None
            self.is_corrupt = True

        self.update_image_display()

    def update_sensory_frame(self, decision, filename):
        """Applica bordo di 4 pixel saturo e sfondi saturi vivi per un impatto sensoriale immediato (Point 3)."""
        if decision == 'KEEP':
            badge_text = "✓  CONSERVATA (KEEP)"
            bg_col = PALETTE["keep_bg"]             # Verde vivido saturo
            border_col = PALETTE["keep_border"]     # Bordo 4px verde foresta intenso
            badge_bg = PALETTE["keep_badge"]
        elif decision == 'DISCARD':
            badge_text = "✕  DA SCARTARE (DISCARD)"
            bg_col = PALETTE["discard_bg"]          # Rosso vivido saturo
            border_col = PALETTE["discard_border"]  # Bordo 4px rosso cremisi intenso
            badge_bg = PALETTE["discard_badge"]
        elif decision == 'ALTRO':
            badge_text = "⚠  ANOMALIA / DA VERIFICARE (ALTRO)"
            bg_col = PALETTE["altro_bg"]            # Giallo/Ambra vivido saturo
            border_col = PALETTE["altro_border"]    # Bordo 4px ambra intenso
            badge_bg = PALETTE["altro_badge"]
        else:
            badge_text = "⏳  IN ATTESA DI DECISIONE (PENDING)"
            bg_col = PALETTE["pending_bg"]          # Grigio/slate neutro distinto
            border_col = PALETTE["pending_border"]  # Bordo 3px blu vivo
            badge_bg = PALETTE["pending_badge"]

        # Cancellazione preventiva per evitare sovrapposizioni
        self.badge_status.config(text="")
        self.badge_status.config(
            text=badge_text,
            bg=badge_bg,
            fg="white",
        )

        # Configurazione Cornice Esterna a 4 pixel saturi e Canvas Interna
        self.viewer_card.config(
            bg=bg_col,
            highlightbackground=border_col,
            highlightcolor=border_col,
            highlightthickness=4,  # Cornice a 4px ad alta saturazione (Point 3)
        )
        self.canvas.config(
            bg=bg_col,
            highlightbackground=border_col,
            highlightcolor=border_col,
            highlightthickness=2,  # Bordo canvas interno coordinato
        )

        # Timestamp decisione
        self.lbl_decision_timestamp.config(text="")
        try:
            self.db_analyzer.cursor.execute(
                "SELECT timestamp FROM photo_reviews WHERE filename = ?", (filename,)
            )
            r = self.db_analyzer.cursor.fetchone()
            if r and r[0]:
                self.lbl_decision_timestamp.config(text=f"Decisa il: {r[0]}")
        except Exception:
            pass

    def update_metadata_sidebar(self, filename, filepath, effective_ts, exif_json, sha256_val, size_bytes, decision):
        """Aggiorna i dati nella sidebar azzerando preventivamente le vecchie stringhe (Point 4)."""
        # 1. Proprietà File
        self.lbl_filename_val.config(text="")
        self.lbl_filename_val.config(text=filename)

        self.lbl_size_val.config(text="")
        self.lbl_size_val.config(text=format_bytes(size_bytes))

        self.lbl_sha_val.config(text="")
        self.lbl_sha_val.config(text=sha256_val[:16] + "..." if sha256_val else "Non calcolato")

        self.lbl_res_val.config(text="")
        try:
            with Image.open(filepath) as tmp_img:
                w, h = tmp_img.size
                mp = (w * h) / 1_000_000
                self.lbl_res_val.config(text=f"{w} × {h} px ({mp:.1f} MP)")
        except Exception:
            self.lbl_res_val.config(text="Non disponibile")

        # 2. Cronologia
        self.lbl_date_val.config(text="")
        self.lbl_date_val.config(text=str(effective_ts))

        self.lbl_state_val.config(text="")
        self.lbl_state_val.config(text=decision)

        self.lbl_origin_val.config(text="")
        origin_text = "Metadati EXIF (DateTimeOriginal)"
        if not exif_json or exif_json == "{}":
            if self.db_analyzer.parse_date_from_filename(filename):
                origin_text = "Nome del file (pattern data/WA)"
            elif str(effective_ts).startswith("1970"):
                origin_text = "Nessuna data (Placeholder 1970)"
            else:
                origin_text = "Data ultima modifica file system"
        self.lbl_origin_val.config(text=origin_text)

        # 3. Parametri Fotocamera & Scatto
        self.lbl_camera_val.config(text="")
        self.lbl_shot_val.config(text="")

        exif_dict = {}
        try:
            exif_dict = json.loads(exif_json) if exif_json else {}
        except Exception:
            pass

        make = exif_dict.get("Make", "")
        model = exif_dict.get("Model", "")
        device_str = f"{make} {model}".strip() or "Non specificato"
        self.lbl_camera_val.config(text=device_str)

        shot_parts = []
        if "ExposureTime" in exif_dict:
            shot_parts.append(f"{exif_dict['ExposureTime']}s")
        if "FNumber" in exif_dict:
            shot_parts.append(f"f/{exif_dict['FNumber']}")
        if "ISOSpeedRatings" in exif_dict:
            shot_parts.append(f"ISO {exif_dict['ISOSpeedRatings']}")
        if "FocalLength" in exif_dict:
            shot_parts.append(f"{exif_dict['FocalLength']}mm")

        self.lbl_shot_val.config(text=" • ".join(shot_parts) if shot_parts else "Dati di scatto assenti")

        # 4. Box Metadati EXIF Completi
        self.txt_exif.config(state=tk.NORMAL)
        self.txt_exif.delete("1.0", tk.END)
        if exif_dict:
            for k, v in sorted(exif_dict.items()):
                self.txt_exif.insert(tk.END, f"{k}: {v}\n")
        else:
            self.txt_exif.insert(tk.END, "(Nessun metadato EXIF memorizzato)")
        self.txt_exif.config(state=tk.DISABLED)

    def update_dashboard_counts(self):
        """Aggiorna le chips numeriche e la barra di avanzamento."""
        if not self.db_analyzer:
            return

        stats = self.db_analyzer.get_quick_stats()
        tot = stats["residue"]
        keep = stats["keep"]
        disc = stats["discard"]
        pend = stats["pending"]
        altro = stats["altro"]

        self.lbl_chip_keep.config(text=f"✓ KEEP: {keep}")
        self.lbl_chip_discard.config(text=f"✕ DISCARD: {disc}")
        self.lbl_chip_altro.config(text=f"⚠ ALTRO: {altro}")
        self.lbl_chip_pending.config(text=f"⏳ PENDING: {pend}")

        evaluated = keep + disc + altro
        pct = (evaluated / tot * 100.0) if tot > 0 else 100.0
        self.progress_var.set(pct)

        cur_num = min(self.current_index + 1, len(self.image_files)) if self.image_files else 0
        self.lbl_progress_text.config(text="")
        self.lbl_progress_text.config(
            text=f"Foto {cur_num} di {len(self.image_files)}  |  Valutate: {evaluated}/{tot} ({pct:.1f}%)"
        )

    def show_completed_state(self):
        """Schermata mostrata al termine della revisione dell'archivio."""
        self.canvas.delete("all")
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        self.canvas.create_text(
            cw // 2, ch // 2 - 20,
            text="🎉 REVISIONE COMPLETATA!",
            font=(FONT_FAMILY, 14, "bold"),
            fill=PALETTE["keep_fg"],
            justify=tk.CENTER,
        )
        self.canvas.create_text(
            cw // 2, ch // 2 + 15,
            text="Tutte le foto della cartella sono state esaminate.\nApri il 'Centro Output' per procedere all'archiviazione e smistamento.",
            font=(FONT_FAMILY, 10),
            fill=PALETTE["text_secondary"],
            justify=tk.CENTER,
        )
        self.badge_status.config(text="✓ TUTTE LE FOTO ESAMINATE", bg=PALETTE["keep_badge"], fg="white")
        self.lbl_footer_status.config(text="Sessione completata. Pronta per l'operazione di output.")

    # --------------------------------------------------------------------------
    # AZIONI OPERATORE: DECISIONI, SALTO, UNDO, NAVIGAZIONE
    # --------------------------------------------------------------------------
    def save_decision(self, decision):
        """Assegna una decisione (KEEP, DISCARD, ALTRO) e passa alla foto successiva."""
        if not self.image_files or not self.db_analyzer or self.is_scanning:
            return

        filename = self.image_files[self.current_index]
        row = self.db_analyzer.get_file_record(filename)
        old_decision = row[0] if row else 'PENDING'

        self.last_action = (filename, old_decision, self.current_index)
        self.db_analyzer.update_decision(filename, decision)

        self.current_index += 1
        self.load_current_image()

    def skip_photo(self):
        """Salta la foto corrente lasciando lo stato invariato (PENDING)."""
        if not self.image_files or self.is_scanning:
            return
        self.current_index += 1
        self.load_current_image()

    def undo_last_action(self):
        """Annulla l'ultima decisione presa e ritorna al file precedente."""
        if not self.last_action or not self.db_analyzer:
            messagebox.showinfo("Annulla", "Nessuna azione recente da annullare.")
            return

        filename, old_decision, target_index = self.last_action
        self.db_analyzer.update_decision(filename, old_decision)

        if filename in self.image_files:
            self.current_index = self.image_files.index(filename)
        else:
            self.current_index = target_index

        self.last_action = None
        self.load_current_image()
        self.lbl_footer_status.config(text=f"Azione annullata per {filename}: ripristinato stato '{old_decision}'.")

    def navigate_relative(self, delta):
        """Navigazione relativa (+1, -1, +10, -10, +100, -100)."""
        if not self.image_files or self.is_scanning:
            return
        new_index = max(0, min(self.current_index + delta, len(self.image_files) - 1))
        if new_index != self.current_index:
            self.current_index = new_index
            self.load_current_image()

    def navigate_to_index(self, index):
        """Naviga direttamente all'indice specificato."""
        if not self.image_files or self.is_scanning:
            return
        new_index = max(0, min(index, len(self.image_files) - 1))
        self.current_index = new_index
        self.load_current_image()

    def do_jump(self):
        """Salto numerico diretto a una foto (1-based)."""
        if not self.image_files or self.is_scanning:
            return
        val = self.entry_jump.get().strip()
        try:
            target = int(val) - 1
            self.navigate_to_index(target)
            self.entry_jump.delete(0, tk.END)
        except ValueError:
            messagebox.showwarning("Valore non valido", "Inserisci un numero di foto valido.")

    def copy_to_clipboard(self, text):
        """Copia una stringa negli appunti di sistema."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            self.lbl_footer_status.config(text=f"Copiato negli appunti: {text}")
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # GUIDA SCORCIATOIE DA TASTIERA (F1)
    # --------------------------------------------------------------------------
    def show_shortcuts_help(self):
        dlg = Toplevel(self.root)
        dlg.title("Guida Scorciatoie da Tastiera - Foto Review 1.7")
        dlg.geometry("560x520")
        dlg.resizable(False, False)
        dlg.configure(bg=PALETTE["bg_surface"])
        dlg.grab_set()

        header = tk.Frame(dlg, bg=PALETTE["bg_header"], padx=16, pady=12)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="⌨️ Comandi & Scorciatoie da Tastiera",
            font=(FONT_FAMILY, 12, "bold"),
            fg="white",
            bg=PALETTE["bg_header"],
        ).pack(anchor=tk.W)
        tk.Label(
            header,
            text="Velocizza la revisione senza usare il mouse",
            font=(FONT_FAMILY, 9),
            fg="#bfdbfe",
            bg=PALETTE["bg_header"],
        ).pack(anchor=tk.W)

        body = tk.Frame(dlg, bg=PALETTE["bg_surface"], padx=18, pady=14)
        body.pack(fill=tk.BOTH, expand=True)

        shortcuts = [
            ("S oppure s", "Conserva la foto attuale (KEEP) e avanza"),
            ("N oppure n", "Scarta la foto attuale (DISCARD) e avanza"),
            ("A oppure a", "Contrassegna come anomalia / da verificare (ALTRO) e avanza"),
            ("Barra Spaziatrice", "Salta foto lasciandola non valutata (PENDING)"),
            ("Ctrl + Z", "Annulla l'ultima decisione presa e torna al file"),
            ("Freccia Su / Giù", "Foto precedente / successiva (±1)"),
            ("Ctrl + Freccia Su / Giù", "Salta indietro / avanti di 10 foto (±10)"),
            ("Page Up / Page Down", "Salta indietro / avanti di 100 foto (±100)"),
            ("Home / End", "Vai alla prima / ultima foto dell'archivio"),
            ("Freccia Sinistra / Destra", "Ruota anteprima di 90° a sinistra o destra"),
            ("Ctrl + O", "Apri Centro Operativo di Output"),
            ("F1", "Visualizza questa finestra di aiuto"),
        ]

        for key, desc in shortcuts:
            row = tk.Frame(body, bg=PALETTE["bg_surface"])
            row.pack(fill=tk.X, pady=3)
            tk.Label(
                row,
                text=key,
                font=(FONT_FAMILY, 9, "bold"),
                fg=PALETTE["primary"],
                bg="#eff6ff",
                highlightthickness=1,
                highlightbackground=PALETTE["border_subtle"],
                padx=6,
                pady=1,
                width=20,
                anchor=tk.W,
            ).pack(side=tk.LEFT)
            tk.Label(
                row,
                text=desc,
                font=(FONT_FAMILY, 9),
                fg=PALETTE["text_primary"],
                bg=PALETTE["bg_surface"],
                anchor=tk.W,
            ).pack(side=tk.LEFT, padx=10)

        btn_close = tk.Button(
            dlg,
            text="Chiudi",
            command=dlg.destroy,
            font=(FONT_FAMILY, 9, "bold"),
            bg="#dbeafe",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=16,
            pady=6,
            cursor="hand2",
        )
        btn_close.pack(pady=12)

    # --------------------------------------------------------------------------
    # CENTRO OPERATIVO DI OUTPUT v1.7 (Tema Blu & Zero Righe Nere)
    # --------------------------------------------------------------------------
    def open_output_panel(self):
        """Apre il Centro Operativo di Output & Azioni Batch v1.7."""
        if not self.output_manager or self.is_scanning:
            messagebox.showwarning("Attenzione", "Seleziona prima una cartella fotografica di lavoro.")
            return

        panel = Toplevel(self.root)
        panel.title("Centro Operativo di Output & Azioni Batch v1.7")
        panel.geometry("920x880")
        panel.minsize(820, 680)
        panel.configure(bg=PALETTE["bg_app"])
        panel.grab_set()

        dry_var = tk.BooleanVar(value=False)

        # Header Modale in Blu Reale Luminoso
        top_bar = tk.Frame(panel, bg=PALETTE["bg_header"], padx=18, pady=12)
        top_bar.pack(fill=tk.X)

        tk.Label(
            top_bar,
            text="📊 Centro Operativo di Output & Smistamento v1.7",
            font=(FONT_FAMILY, 12, "bold"),
            fg="white",
            bg=PALETTE["bg_header"],
        ).pack(anchor=tk.W)
        tk.Label(
            top_bar,
            text="Esegui operazioni definitive, archiviazione cronologica e query personalizzate.",
            font=(FONT_FAMILY, 9),
            fg="#bfdbfe",
            bg=PALETTE["bg_header"],
        ).pack(anchor=tk.W)

        # Area Scorrevole Principale
        canvas = tk.Canvas(panel, bg=PALETTE["bg_app"], highlightthickness=0)
        scrollbar = tk.Scrollbar(panel, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=PALETTE["bg_app"], padx=14, pady=10)

        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _configure_canvas_width(event):
            canvas.itemconfig(window_id, width=event.width)

        canvas.bind("<Configure>", _configure_canvas_width)
        canvas.configure(yscrollcommand=scrollbar.set)

        # Supporto rotellina del mouse su Windows
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        panel.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._out_panel = panel
        self._out_body = body
        self._out_dry_var = dry_var

        # 1. Cruscotto Statistico
        self.build_output_dashboard()

        # 2. Banner Simulazione
        self.build_output_dry_banner()

        # 3. Gruppi Operativi
        self.build_output_group_a()
        self.build_output_group_b()
        self.build_output_group_c()
        self.build_output_group_d()

        # Chiusura
        btn_close = tk.Button(
            body,
            text="Chiudi Centro Output",
            command=self._close_output_panel,
            font=(FONT_FAMILY, 10, "bold"),
            bg="#dbeafe",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=20,
            pady=8,
            cursor="hand2",
        )
        btn_close.pack(pady=16)

        panel.protocol("WM_DELETE_WINDOW", self._close_output_panel)

    def _close_output_panel(self):
        try:
            self._out_panel.unbind_all("<MouseWheel>")
            self._out_panel.destroy()
        except Exception:
            pass

        # Ricarica il residuo dell'archivio principale
        try:
            if self.db_analyzer:
                self.image_files = self.db_analyzer.get_sorted_files()
                if self.current_index >= len(self.image_files):
                    self.current_index = max(0, len(self.image_files) - 1)
                if self.image_files:
                    self.load_current_image()
                else:
                    self.show_completed_state()
                self.update_dashboard_counts()
        except Exception:
            pass

    def _out_is_dry(self):
        try:
            return bool(self._out_dry_var.get())
        except Exception:
            return False

    def build_output_dashboard(self):
        card = tk.Frame(
            self._out_body,
            bg=PALETTE["bg_surface"],
            highlightbackground=PALETTE["border_blue"],
            highlightcolor=PALETTE["border_blue"],
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        card.pack(fill=tk.X, pady=(0, 8))

        top_row = tk.Frame(card, bg=PALETTE["bg_surface"])
        top_row.pack(fill=tk.X)

        tk.Label(
            top_row,
            text="📈 RIEPILOGO STATISTICO DELL'ARCHIVIO",
            font=(FONT_FAMILY, 9, "bold"),
            fg=PALETTE["primary"],
            bg=PALETTE["bg_surface"],
        ).pack(side=tk.LEFT)

        btn_refresh = tk.Button(
            top_row,
            text="🔄 Aggiorna Dati",
            command=self.refresh_output_stats,
            font=(FONT_FAMILY, 8),
            bg="#eff6ff",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=2,
            cursor="hand2",
        )
        btn_refresh.pack(side=tk.RIGHT)

        # Griglia di metriche card (con bordi azzurri, no nero)
        self.metrics_container = tk.Frame(card, bg=PALETTE["bg_surface"], pady=6)
        self.metrics_container.pack(fill=tk.X)

        self.lbl_m_tot = self.create_metric_tile(self.metrics_container, "Totale File", "—", PALETTE["primary"])
        self.lbl_m_res = self.create_metric_tile(self.metrics_container, "Da Elaborare", "—", "#1d4ed8")
        self.lbl_m_proc = self.create_metric_tile(self.metrics_container, "Già Elaborati", "—", "#64748b")
        self.lbl_m_keep = self.create_metric_tile(self.metrics_container, "KEEP (Conserva)", "—", "#15803d")
        self.lbl_m_disc = self.create_metric_tile(self.metrics_container, "DISCARD (Scarta)", "—", "#dc2626")
        self.lbl_m_pend = self.create_metric_tile(self.metrics_container, "PENDING (In attesa)", "—", "#2563eb")
        self.lbl_m_altro = self.create_metric_tile(self.metrics_container, "ALTRO (Anomalie)", "—", "#d97706")

        self.refresh_output_stats()

    def create_metric_tile(self, parent, title, val, color):
        frame = tk.Frame(
            parent,
            bg="#f8fafc",
            highlightbackground=PALETTE["border_subtle"],
            highlightcolor=PALETTE["border_subtle"],
            highlightthickness=1,
            padx=8,
            pady=6,
        )
        frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        tk.Label(frame, text=title, font=(FONT_FAMILY, 7), fg=PALETTE["text_secondary"], bg="#f8fafc").pack()
        lbl_v = tk.Label(frame, text=val, font=(FONT_FAMILY, 12, "bold"), fg=color, bg="#f8fafc")
        lbl_v.pack()
        return lbl_v

    def refresh_output_stats(self):
        try:
            stats = self.output_manager.get_statistics()
        except Exception:
            stats = None

        if not stats:
            return None

        self.lbl_m_tot.config(text=str(stats["total"]))
        self.lbl_m_res.config(text=str(stats["to_process"]))
        self.lbl_m_proc.config(text=str(stats["processed"]))
        self.lbl_m_keep.config(text=str(stats["kept"]))
        self.lbl_m_disc.config(text=str(stats["discarded"]))
        self.lbl_m_pend.config(text=f"{stats['pending']} ({stats['pending_nodate']} s.d.)")
        self.lbl_m_altro.config(text=str(stats["altro"]))

        self.update_action_button_badges(stats)
        return stats

    def build_output_dry_banner(self):
        banner = tk.Frame(
            self._out_body,
            bg="#fffbeb",
            highlightbackground="#fde047",
            highlightthickness=1,
            padx=12,
            pady=8,
        )
        banner.pack(fill=tk.X, pady=(0, 8))

        chk = tk.Checkbutton(
            banner,
            text="Attiva Modalità Simulazione (Dry-Run) — Calcola le operazioni senza modificare o spostare alcun file",
            variable=self._out_dry_var,
            font=(FONT_FAMILY, 9, "bold"),
            bg="#fffbeb",
            fg="#92400e",
            activebackground="#fffbeb",
            command=self._on_dry_run_toggled,
        )
        chk.pack(side=tk.LEFT)

        self.lbl_dry_indicator = tk.Label(
            banner,
            text="",
            font=(FONT_FAMILY, 8, "italic"),
            bg="#fffbeb",
            fg="#b45309",
        )
        self.lbl_dry_indicator.pack(side=tk.RIGHT)

    def _on_dry_run_toggled(self):
        if self._out_is_dry():
            self.lbl_dry_indicator.config(text="⚡ SIMULAZIONE ATTIVA: Nessun file verrà alterato")
        else:
            self.lbl_dry_indicator.config(text="")

    def _out_confirm(self, title, n, dest_txt):
        if n <= 0:
            messagebox.showinfo("Nessun elemento", f"{title}: non ci sono file da elaborare in questa categoria.")
            return False
        extra = " [MODALITÀ SIMULAZIONE]" if self._out_is_dry() else ""
        return messagebox.askyesno(
            f"Conferma Operazione{extra}",
            f"Operazione: {title}{extra}\n\n"
            f"Numero di file coinvolti: {n}\n"
            f"Destinazione prevista: {dest_txt}\n\n"
            f"{'ATTENZIONE: Procedere con lo spostamento fisico?' if not self._out_is_dry() else 'Eseguire la simulazione di calcolo?'}",
        )

    def _out_done(self, title, count, errs):
        msg = f"{title}\n\nFile elaborati con successo: {count}"
        if errs:
            msg += f"\n\nNote / Errori ({len(errs)}):\n" + "\n".join(errs[:5])
            if len(errs) > 5:
                msg += f"\n... + altri {len(errs) - 5} errori."
        if self._out_is_dry():
            msg = "[SIMULAZIONE COMPLETATA - NESSUN FILE TOCCATO]\n\n" + msg
        messagebox.showinfo("Operazione Completata", msg)
        self.refresh_output_stats()

    # --------------------------------------------------------------------------
    # GRUPPO A: ESAMINATE
    # --------------------------------------------------------------------------
    def build_output_group_a(self):
        card = self.create_output_card("A — FILE ESAMINATI DALL'OPERATORE (KEEP / DISCARD / ALTRO)")

        # 1. Conservati KEEP
        sec_keep = self.create_sub_action_box(
            card,
            "📁 Archiviazione Foto da Conservare (KEEP)",
            "Organizza i file approvati in una struttura di cartelle cronologica pulita.",
        )
        row_k = tk.Frame(sec_keep, bg="white")
        row_k.pack(fill=tk.X, pady=4)

        self.btn_keep_year = tk.Button(
            row_k,
            text="📅 Archivia per Anno (es. 2023/)",
            command=self.run_keep_year,
            font=(FONT_FAMILY, 9, "bold"),
            bg="#dcfce7",
            fg="#15803d",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.btn_keep_year.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)

        self.btn_keep_month = tk.Button(
            row_k,
            text="📆 Archivia per Anno e Mese (es. 2023/ott 2023/)",
            command=self.run_keep_month,
            font=(FONT_FAMILY, 9, "bold"),
            bg="#dcfce7",
            fg="#15803d",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.btn_keep_month.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 2. Scartati DISCARD
        sec_disc = self.create_sub_action_box(
            card,
            "🗑️ Smaltimento Foto Scartate (DISCARD)",
            "Elimina i file contrassegnati per liberare spazio o isolali in una cartella.",
        )
        row_d = tk.Frame(sec_disc, bg="white")
        row_d.pack(fill=tk.X, pady=4)

        self.btn_disc_trash = tk.Button(
            row_d,
            text="🗑 Cestina nel Cestino di Windows (Recuperabile)",
            command=self.run_trash_discards,
            font=(FONT_FAMILY, 9, "bold"),
            bg="#fee2e2",
            fg="#991b1b",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.btn_disc_trash.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)

        self.btn_disc_move = tk.Button(
            row_d,
            text="📁 Sposta in cartella scartate/ (Conservativo)",
            command=self.run_move_discards,
            font=(FONT_FAMILY, 9),
            bg="#eff6ff",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.btn_disc_move.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 3. Anomalie ALTRO
        sec_altro = self.create_sub_action_box(
            card,
            "⚠️ Isolamento Anomalie & File Speciali (ALTRO)",
            "Sposta i file corrotti, a 0 byte o contrassegnati per verifica separata.",
        )
        row_a = tk.Frame(sec_altro, bg="white")
        row_a.pack(fill=tk.X, pady=4)

        self.btn_altro_move = tk.Button(
            row_a,
            text="⚠️ Sposta in ALTRO_da_verificare/",
            command=self.run_move_altro,
            font=(FONT_FAMILY, 9, "bold"),
            bg="#fef3c7",
            fg="#92400e",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.btn_altro_move.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def run_keep_year(self):
        n, _ = self.output_manager.preview("decision = 'KEEP'")
        if not self._out_confirm("Archiviazione KEEP per Anno", n, "Cartelle 'YYYY/'"):
            return
        c, e = self.output_manager.archive_keep_by_year(dry_run=self._out_is_dry())
        self._out_done("Archiviazione KEEP per Anno", c, e)

    def run_keep_month(self):
        n, _ = self.output_manager.preview("decision = 'KEEP'")
        if not self._out_confirm("Archiviazione KEEP per Anno/Mese", n, "Cartelle 'YYYY/mmm YYYY/'"):
            return
        c, e = self.output_manager.archive_keep_by_year_month(dry_run=self._out_is_dry())
        self._out_done("Archiviazione KEEP per Anno/Mese", c, e)

    def run_trash_discards(self):
        n, _ = self.output_manager.preview("decision = 'DISCARD'")
        if not self._out_confirm("Cestinatura DISCARD", n, "Cestino di Windows"):
            return
        c, e = self.output_manager.trash_discards(dry_run=self._out_is_dry())
        self._out_done("Cestinatura DISCARD", c, e)

    def run_move_discards(self):
        n, _ = self.output_manager.preview("decision = 'DISCARD'")
        if not self._out_confirm("Spostamento DISCARD", n, "Cartella 'scartate/'"):
            return
        c, e = self.output_manager.move_discards(dry_run=self._out_is_dry())
        self._out_done("Spostamento DISCARD in scartate/", c, e)

    def run_move_altro(self):
        n, _ = self.output_manager.preview("decision = 'ALTRO'")
        if not self._out_confirm("Isolamento ALTRO", n, "Cartella 'ALTRO_da_verificare/'"):
            return
        c, e = self.output_manager.move_altro(dry_run=self._out_is_dry())
        self._out_done("Isolamento ALTRO", c, e)

    # --------------------------------------------------------------------------
    # GRUPPO B: NON ESAMINATE (PENDING)
    # --------------------------------------------------------------------------
    def build_output_group_b(self):
        card = self.create_output_card("B — FILE NON ESAMINATI (PENDING)")

        sec_p = self.create_sub_action_box(
            card,
            "📦 Gestione ed Isolamento Residuo PENDING",
            "Sposta le foto ancora in attesa per alleggerire la cartella di lavoro o affrontarle in sessioni successive.",
        )
        row_p = tk.Frame(sec_p, bg="white")
        row_p.pack(fill=tk.X, pady=4)

        self.btn_pend_move = tk.Button(
            row_p,
            text="📦 Sposta tutti i PENDING in Da_rivedere/",
            command=self.run_move_pending,
            font=(FONT_FAMILY, 9, "bold"),
            bg="#eff6ff",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
        )
        self.btn_pend_move.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)

        self.btn_pend_nodate = tk.Button(
            row_p,
            text="🗓 Isola PENDING senza data (1970) in Da_datare/",
            command=self.run_isolate_nodate,
            font=(FONT_FAMILY, 9),
            bg="#f1f5f9",
            fg=PALETTE["text_primary"],
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
        )
        self.btn_pend_nodate.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Azioni Massive
        sec_bulk = self.create_sub_action_box(
            card,
            "⚡ Decisioni Massive sui PENDING (Solo Marcatura Database)",
            "Modifica in blocco la decisione di tutti i PENDING senza spostare file.",
        )
        row_b = tk.Frame(sec_bulk, bg="white")
        row_b.pack(fill=tk.X, pady=4)

        btn_b_keep = tk.Button(
            row_b,
            text="✓ Marca tutti i PENDING come KEEP",
            command=lambda: self.run_bulk_decision("KEEP"),
            font=(FONT_FAMILY, 9),
            bg="#dcfce7",
            fg="#15803d",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
        )
        btn_b_keep.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)

        btn_b_disc = tk.Button(
            row_b,
            text="✕ Marca tutti i PENDING come DISCARD",
            command=lambda: self.run_bulk_decision("DISCARD"),
            font=(FONT_FAMILY, 9),
            bg="#fee2e2",
            fg="#991b1b",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
        )
        btn_b_disc.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def run_move_pending(self):
        n, _ = self.output_manager.preview("decision = 'PENDING'")
        if not self._out_confirm("Spostamento PENDING", n, "Cartella 'Da_rivedere/'"):
            return
        c, e = self.output_manager.move_pending(dry_run=self._out_is_dry())
        self._out_done("Spostamento PENDING in Da_rivedere/", c, e)

    def run_isolate_nodate(self):
        n, _ = self.output_manager.preview(
            "decision = 'PENDING' AND (effective_timestamp IS NULL OR effective_timestamp LIKE '1970%')"
        )
        if not self._out_confirm("Isolamento PENDING senza data", n, "Cartella 'Da_datare/'"):
            return
        c, e = self.output_manager.isolate_pending_nodate(dry_run=self._out_is_dry())
        self._out_done("Isolamento PENDING senza data in Da_datare/", c, e)

    def run_bulk_decision(self, to_decision):
        st = self.output_manager.get_statistics() or {}
        n = st.get("pending", 0)
        if n <= 0:
            messagebox.showinfo("Niente da fare", "Nessun file PENDING residuo da marcare.")
            return

        extra = " [SIMULAZIONE]" if self._out_is_dry() else ""
        if not messagebox.askyesno(
            f"Conferma Decisione Massiva{extra}",
            f"Vuoi davvero marcare tutti i {n} file PENDING come '{to_decision}'?{extra}\n\n"
            f"Nota: questa operazione aggiorna solo i metadati nel database e non sposta alcun file.",
        ):
            return

        if self._out_is_dry():
            self._out_done(f"Rimarcatura PENDING -> {to_decision}", n, [])
            return

        c, e = self.output_manager.bulk_set_decision(to_decision, "PENDING")
        self._out_done(f"Rimarcatura PENDING -> {to_decision}", c, e)

    # --------------------------------------------------------------------------
    # GRUPPO C: QUERY SQL LIBERA & PRESET
    # --------------------------------------------------------------------------
    def build_output_group_c(self):
        card = self.create_output_card("C — QUERY SQL LIBERA & ESTRAZIONE PERSONALIZZATA")

        tk.Label(
            card,
            text="Filtra ed estrai file in base a qualsiasi criterio utilizzando query SQL. Deve restituire la colonna 'filename'.",
            font=(FONT_FAMILY, 9),
            fg=PALETTE["text_secondary"],
            bg="white",
        ).pack(anchor=tk.W, pady=(0, 6))

        # Preset a tendina
        preset_row = tk.Frame(card, bg="white")
        preset_row.pack(fill=tk.X, pady=2)

        tk.Label(
            preset_row,
            text="Query Predefinite (Preset):",
            font=(FONT_FAMILY, 9, "bold"),
            fg=PALETTE["text_primary"],
            bg="white",
        ).pack(side=tk.LEFT, padx=(0, 6))

        preset_titles = [p[0] for p in PRESET_QUERIES]
        self.cmb_preset = ttk.Combobox(
            preset_row,
            values=preset_titles,
            state="readonly",
            width=45,
            font=(FONT_FAMILY, 9),
        )
        self.cmb_preset.current(0)
        self.cmb_preset.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.cmb_preset.bind("<<ComboboxSelected>>", self._on_preset_selected)

        # Editor di Query
        tk.Label(
            card,
            text="Istruzione SQL (SELECT):",
            font=(FONT_FAMILY, 8, "bold"),
            fg=PALETTE["text_secondary"],
            bg="white",
        ).pack(anchor=tk.W, pady=(6, 1))

        self.txt_sql = tk.Text(
            card,
            font=("Consolas", 9),
            height=3,
            bg="#f8fafc",
            fg=PALETTE["text_primary"],
            highlightthickness=1,
            highlightbackground=PALETTE["border_blue"],
            bd=0,
            padx=8,
            pady=6,
        )
        self.txt_sql.pack(fill=tk.X, pady=2)
        self.txt_sql.insert("1.0", "SELECT filename FROM photo_reviews WHERE decision = 'KEEP'")

        # Colonne disponibili
        tk.Label(
            card,
            text="Colonne disponibili: filename, size_bytes, sha256, exif_data, effective_timestamp, decision, output_action",
            font=(FONT_FAMILY, 8, "italic"),
            fg=PALETTE["text_muted"],
            bg="white",
        ).pack(anchor=tk.W, pady=(1, 4))

        # Destinazione e Pulsanti
        dest_row = tk.Frame(card, bg="white")
        dest_row.pack(fill=tk.X, pady=4)

        tk.Label(dest_row, text="Cartella Destinazione:", font=(FONT_FAMILY, 9, "bold"), bg="white").pack(side=tk.LEFT, padx=(0, 6))
        self.sql_dest_var = tk.StringVar(value="Selezionati_da_Query")
        self.entry_sql_dest = tk.Entry(
            dest_row,
            textvariable=self.sql_dest_var,
            font=("Consolas", 9),
            width=26,
            highlightthickness=1,
            highlightbackground=PALETTE["border_blue"],
            relief=tk.FLAT,
        )
        self.entry_sql_dest.pack(side=tk.LEFT, padx=(0, 10))

        btn_preview = tk.Button(
            dest_row,
            text="🔍 Anteprima Risultati",
            command=self.run_sql_preview,
            font=(FONT_FAMILY, 9),
            bg="#eff6ff",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
        )
        btn_preview.pack(side=tk.LEFT, padx=3)

        btn_exec = tk.Button(
            dest_row,
            text="🚀 Esegui e Sposta i File",
            command=self.run_sql_exec,
            font=(FONT_FAMILY, 9, "bold"),
            bg="#2563eb",
            fg="white",
            activebackground="#1d4ed8",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=4,
            cursor="hand2",
        )
        btn_exec.pack(side=tk.LEFT, padx=3)

        # Tabella Anteprima Risultati Integrata (con bordo azzurro, no nero)
        self.frame_sql_preview = tk.Frame(
            card,
            bg="#f8fafc",
            highlightthickness=1,
            highlightbackground=PALETTE["border_subtle"],
            padx=6,
            pady=4,
        )
        self.lbl_sql_preview_info = tk.Label(
            self.frame_sql_preview,
            text="Clicca 'Anteprima Risultati' per verificare i file trovati prima di eseguire lo spostamento.",
            font=(FONT_FAMILY, 8, "italic"),
            fg=PALETTE["text_muted"],
            bg="#f8fafc",
        )
        self.lbl_sql_preview_info.pack(anchor=tk.W)

        cols = ("Nome File", "Dimensione", "Data", "Decisione", "Stato Azione")
        self.tree_sql = ttk.Treeview(self.frame_sql_preview, columns=cols, show="headings", height=4)
        for c in cols:
            self.tree_sql.heading(c, text=c)
            self.tree_sql.column(c, width=120)
        self.tree_sql.column("Nome File", width=200)
        self.tree_sql.pack(fill=tk.X, expand=True, pady=3)
        self.frame_sql_preview.pack(fill=tk.X, pady=(4, 0))

    def _on_preset_selected(self, event):
        idx = self.cmb_preset.current()
        if idx > 0 and idx < len(PRESET_QUERIES):
            sql_text = PRESET_QUERIES[idx][1]
            self.txt_sql.delete("1.0", tk.END)
            self.txt_sql.insert("1.0", sql_text)

    def run_sql_preview(self):
        query = self.txt_sql.get("1.0", tk.END).strip()
        count, records, errors = self.output_manager.preview_query_detailed(query, limit=50)

        # Pulisci treeview precedente (Point 4)
        for item in self.tree_sql.get_children():
            self.tree_sql.delete(item)

        self.lbl_sql_preview_info.config(text="")
        if errors:
            self.lbl_sql_preview_info.config(text=f"⚠️ Errore SQL: {errors[0]}", fg="#dc2626")
            return

        self.lbl_sql_preview_info.config(
            text=f"Trovate {count} righe corrispondenti (mostrate le prime {len(records)}):",
            fg=PALETTE["primary"],
        )

        for rec in records:
            sz_str = format_bytes(rec["size_bytes"])
            self.tree_sql.insert(
                "",
                tk.END,
                values=(rec["filename"], sz_str, rec["effective_timestamp"], rec["decision"], rec["output_action"]),
            )

    def run_sql_exec(self):
        query = self.txt_sql.get("1.0", tk.END).strip()
        dest = self.sql_dest_var.get().strip() or "Selezionati_da_Query"

        count, _, errors = self.output_manager.preview_custom_query(query)
        if errors:
            messagebox.showerror("Errore Query SQL", "\n".join(errors))
            return

        if not self._out_confirm(f"Query SQL personalizzata -> {dest}/", count, f"Cartella '{dest}/'"):
            return

        c, e = self.output_manager.process_custom_query(query, dest, dry_run=self._out_is_dry())
        self._out_done(f"Query SQL completata -> {dest}/", c, e)

    # --------------------------------------------------------------------------
    # GRUPPO D: REPORT, AUDIT & MANUTENZIONE
    # --------------------------------------------------------------------------
    def build_output_group_d(self):
        card = self.create_output_card("D — REPORT, AUDIT, DUPLICATI & MANUTENZIONE")

        sec_rep = self.create_sub_action_box(
            card,
            "📑 Esportazione Report & Audit",
            "Genera fogli di calcolo CSV completi con le decisioni e i duplicati certi.",
        )
        row_r = tk.Frame(sec_rep, bg="white")
        row_r.pack(fill=tk.X, pady=4)

        btn_csv = tk.Button(
            row_r,
            text="📄 Esporta Report Decisioni (CSV)",
            command=self.run_export_csv,
            font=(FONT_FAMILY, 9),
            bg="#eff6ff",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=5,
            cursor="hand2",
        )
        btn_csv.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)

        btn_log = tk.Button(
            row_r,
            text="📜 Visualizza Registro Operazioni (TXT)",
            command=self.run_view_log,
            font=(FONT_FAMILY, 9),
            bg="#eff6ff",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=5,
            cursor="hand2",
        )
        btn_log.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)

        btn_folder = tk.Button(
            row_r,
            text="📁 Apri Cartella di Lavoro",
            command=lambda: OutputManager.open_path(self.folder_path),
            font=(FONT_FAMILY, 9),
            bg="#eff6ff",
            fg=PALETTE["primary"],
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=5,
            cursor="hand2",
        )
        btn_folder.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Manutenzione Reset
        sec_maint = self.create_sub_action_box(
            card,
            "🛡️ Manutenzione: Ripristino Marcature Elaborate",
            "Cancella le marcature 'elaborato' nel DB per consentire una nuova revisione completa. Usalo solo dopo aver riportato i file nella cartella principale.",
        )
        row_m = tk.Frame(sec_maint, bg="white")
        row_m.pack(fill=tk.X, pady=4)

        btn_reset = tk.Button(
            row_m,
            text="⚠️ Azzera Marcature Elaborate (Reset stato batch)",
            command=self.run_reset_flags,
            font=(FONT_FAMILY, 8, "bold"),
            bg="#fee2e2",
            fg="#991b1b",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
        )
        btn_reset.pack(side=tk.LEFT)

    def run_export_csv(self):
        path, errs = self.output_manager.export_csv()
        if errs:
            messagebox.showerror("Errore Export CSV", "\n".join(errs))
        else:
            ans = messagebox.askyesno(
                "Export CSV Completato",
                f"Report salvato con successo in:\n{path}\n\nVuoi aprire il file CSV generato adesso?",
            )
            if ans:
                OutputManager.open_path(path)

    def run_view_log(self):
        log_path = os.path.join(self.folder_path, "output_log.txt")
        if not os.path.exists(log_path):
            messagebox.showinfo("Registro Operazioni", "Il file di log 'output_log.txt' non è ancora stato creato.")
            return
        OutputManager.open_path(log_path)

    def run_reset_flags(self):
        if not messagebox.askyesno(
            "Conferma Reset Marcature",
            "Vuoi davvero azzerare tutte le marcature 'elaborato' nel database?\n\n"
            "Questa operazione non sposta fisicamente i file, ma permette al programma di considerare nuovamente tutti i file presenti.\n"
            "Utilizzare SOLO se sono stati riportati manualmente i file nella cartella principale.",
        ):
            return

        c, e = self.output_manager.reset_processed_flags()
        if e:
            messagebox.showerror("Errore Reset", "\n".join(e))
        else:
            self._out_done("Reset marcature elaborate", c, [])

    # --------------------------------------------------------------------------
    # UTILITY COSTRUZIONE CARD OUTPUT (Bordi azzurri morbidi)
    # --------------------------------------------------------------------------
    def create_output_card(self, title):
        card = tk.Frame(
            self._out_body,
            bg="white",
            highlightthickness=1,
            highlightbackground=PALETTE["border_blue"],
            padx=14,
            pady=12,
        )
        card.pack(fill=tk.X, pady=(0, 8))

        lbl = tk.Label(
            card,
            text=title,
            font=(FONT_FAMILY, 10, "bold"),
            fg=PALETTE["primary"],
            bg="white",
        )
        lbl.pack(anchor=tk.W, pady=(0, 6))

        div = tk.Frame(card, bg=PALETTE["border_subtle"], height=1)
        div.pack(fill=tk.X, pady=(0, 8))
        return card

    def create_sub_action_box(self, parent, title, desc):
        box = tk.Frame(parent, bg="white", pady=4)
        box.pack(fill=tk.X, pady=3)

        tk.Label(
            box,
            text=title,
            font=(FONT_FAMILY, 9, "bold"),
            fg=PALETTE["text_primary"],
            bg="white",
        ).pack(anchor=tk.W)

        tk.Label(
            box,
            text=desc,
            font=(FONT_FAMILY, 8),
            fg=PALETTE["text_secondary"],
            bg="white",
        ).pack(anchor=tk.W, pady=(1, 3))

        return box

    def update_action_button_badges(self, stats):
        """Aggiorna le etichette dei pulsanti con i conteggi esatti disponibili."""
        try:
            k = stats.get("kept", 0)
            d = stats.get("discarded", 0)
            p = stats.get("pending", 0)
            a = stats.get("altro", 0)

            self.btn_keep_year.config(text=f"📅 Archivia per Anno [{k} file]")
            self.btn_keep_month.config(text=f"📆 Archivia per Anno/Mese [{k} file]")
            self.btn_disc_trash.config(text=f"🗑 Cestina nel Cestino [{d} file]")
            self.btn_disc_move.config(text=f"📁 Sposta in scartate/ [{d} file]")
            self.btn_altro_move.config(text=f"⚠️ Sposta in ALTRO_da_verificare/ [{a} file]")
            self.btn_pend_move.config(text=f"📦 Sposta PENDING in Da_rivedere/ [{p} file]")
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # CHIUSURA APPLICAZIONE
    # --------------------------------------------------------------------------
    def exit_app(self):
        self.save_session_state()
        if self.db_analyzer:
            self.db_analyzer.close()
        self.root.destroy()