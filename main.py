"""Foto_Review v1.7.0 - Entry Point dell'applicazione.

Revisione fotografica cronologica, deduplicazione e archiviazione batch multithread intelligente.
"""
import sys
import tkinter as tk
from ui import PhotoReviewerApp


def enable_windows_dpi_awareness():
    """Abilita la consapevolezza DPI su Windows per grafica e font nitidi ad alta risoluzione."""
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            from ctypes import windll
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    enable_windows_dpi_awareness()
    root = tk.Tk()
    app = PhotoReviewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()