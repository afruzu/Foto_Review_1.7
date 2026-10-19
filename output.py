"""Foto_Review v1.7.0 - Modulo operativo di output.

Filosofia: aggredire archivi non strutturati e compattarli.
Solo SPOSTA (struttura) o CESTINA (Cestino Windows). Niente copie.
Ogni azione fisica e' tracciata in DB (output_action/output_timestamp) + output_log.txt.
"""
import os
import csv
import shutil
import sqlite3
from datetime import datetime

try:
    from send2trash import send2trash
    _HAS_SEND2TRASH = True
except Exception:
    _HAS_SEND2TRASH = False


FORBIDDEN_SQL_KEYWORDS = ("insert", "update", "delete", "drop", "alter",
                          "create", "replace", "truncate", "attach", "detach",
                          "vacuum", "pragma")

# Abbreviazioni mesi italiane per cartelle leggibili ("gen 2023", "dic 2023").
# La modalita per solo-anno resta invariata (cartella "2023").
MESE_ITA = {"01": "gen", "02": "feb", "03": "mar", "04": "apr",
            "05": "mag", "06": "giu", "07": "lug", "08": "ago",
            "09": "set", "10": "ott", "11": "nov", "12": "dic"}

# Preset di query SQL pronte all'uso per utenti comuni ed avanzati
PRESET_QUERIES = [
    ("-- Scegli una query predefinita --", ""),
    ("1. Tutte le foto da Conservare (KEEP)", "SELECT filename FROM photo_reviews WHERE decision = 'KEEP'"),
    ("2. Tutte le foto da Scartare (DISCARD)", "SELECT filename FROM photo_reviews WHERE decision = 'DISCARD'"),
    ("3. Tutte le anomalie da verificare (ALTRO)", "SELECT filename FROM photo_reviews WHERE decision = 'ALTRO'"),
    ("4. Foto ancora in attesa (PENDING)", "SELECT filename FROM photo_reviews WHERE decision = 'PENDING'"),
    ("5. Foto dell'anno 2024", "SELECT filename FROM photo_reviews WHERE effective_timestamp LIKE '2024%'"),
    ("6. Foto dell'anno 2023", "SELECT filename FROM photo_reviews WHERE effective_timestamp LIKE '2023%'"),
    ("7. Foto storiche (2022 o antecedenti)", "SELECT filename FROM photo_reviews WHERE effective_timestamp < '2023-01-01'"),
    ("8. File grandi (> 5 MB)", "SELECT filename FROM photo_reviews WHERE size_bytes > 5242880"),
    ("9. File leggeri (< 500 KB)", "SELECT filename FROM photo_reviews WHERE size_bytes < 512000"),
    ("10. Foto da WhatsApp (IMG-WA)", "SELECT filename FROM photo_reviews WHERE filename LIKE '%-WA%' OR filename LIKE 'IMG-WA%'"),
    ("11. Foto senza EXIF nativo", "SELECT filename FROM photo_reviews WHERE exif_data = '{}' OR exif_data IS NULL"),
    ("12. Foto con data 1970 (placeholder o mancante)", "SELECT filename FROM photo_reviews WHERE effective_timestamp IS NULL OR effective_timestamp LIKE '1970%'"),
]


class OutputManager:
    def __init__(self, folder_path, db_path):
        self.folder_path = folder_path
        self.db_path = db_path
        self.log_path = os.path.join(self.folder_path, "output_log.txt")

    # ---------- helpers di base ----------
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        return conn

    def _has_output_cols(self, cursor):
        try:
            cursor.execute("PRAGMA table_info(photo_reviews)")
            cols = {r[1] for r in cursor.fetchall()}
            return "output_action" in cols and "output_timestamp" in cols
        except Exception:
            return False

    def _residue_filter(self, has_cols):
        return "AND output_action IS NULL" if has_cols else ""

    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _append_log(self, message):
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"[{self._now()}] {message}\n")
        except Exception:
            pass

    def _mark_processed(self, cursor, filename, action_label):
        cursor.execute(
            "UPDATE photo_reviews SET output_action = ?, output_timestamp = ? WHERE filename = ?",
            (action_label, self._now(), filename),
        )

    def _resolve_collision(self, dst_path):
        """Se dst esiste, genera nome(1).ext, nome(2).ext ..."""
        if not os.path.exists(dst_path):
            return dst_path
        base, ext = os.path.splitext(dst_path)
        i = 1
        while True:
            cand = f"{base}({i}){ext}"
            if not os.path.exists(cand):
                return cand
            i += 1

    # ---------- statistiche ----------
    def get_statistics(self):
        """Ritorna totali + residuo da elaborare + gia' elaborati."""
        if not os.path.exists(self.db_path):
            return None
        conn = self._connect()
        cur = conn.cursor()
        has_cols = self._has_output_cols(cur)
        res_filter = self._residue_filter(has_cols)

        def count(where, params=()):
            cur.execute(f"SELECT count(*) FROM photo_reviews WHERE {where}", params)
            return cur.fetchone()[0]

        total = count("1=1")
        if has_cols:
            processed = count("output_action IS NOT NULL")
        else:
            processed = 0
        to_process = total - processed
        kept = count(f"decision = 'KEEP' {res_filter}")
        discarded = count(f"decision = 'DISCARD' {res_filter}")
        pending = count(f"decision = 'PENDING' {res_filter}")
        altro = count(f"decision = 'ALTRO' {res_filter}")
        pending_nodate = count(
            f"decision = 'PENDING' {res_filter} "
            "AND (effective_timestamp IS NULL OR effective_timestamp LIKE '1970%')"
        )
        conn.close()
        return {
            "total": total, "processed": processed, "to_process": to_process,
            "kept": kept, "discarded": discarded, "pending": pending,
            "altro": altro, "pending_nodate": pending_nodate,
        }

    def preview(self, where_clause, params=(), limit=5):
        """Dry-run: conta e mostra i primi N senza toccare nulla."""
        rows = self._fetch_filenames(where_clause, params)
        return len(rows), rows[:limit]

    def _transfer(self, filenames, dest_rel=None, mode="move",
                  action_prefix="SPOSTATO", dry_run=False):
        """Motore unico: move -> sottocartella | trash -> Cestino Windows."""
        moved, errors = 0, []
        dest_abs = os.path.join(self.folder_path, dest_rel) if dest_rel else None
        if mode == "move" and not dry_run:
            os.makedirs(dest_abs, exist_ok=True)
        conn = self._connect()
        cur = conn.cursor()
        has_cols = self._has_output_cols(cur)
        for filename in filenames:
            src = os.path.join(self.folder_path, filename)
            if not os.path.exists(src):
                if has_cols and not dry_run:
                    self._mark_processed(cur, filename, f"{action_prefix} (assente)")
                continue
            try:
                if dry_run:
                    moved += 1
                    continue
                if mode == "trash":
                    if _HAS_SEND2TRASH:
                        send2trash(src)
                        label = "CESTINATO (Cestino di Windows)"
                    else:
                        os.remove(src)
                        label = "ELIMINATO definitivo (no send2trash)"
                else:
                    dst = self._resolve_collision(os.path.join(dest_abs, filename))
                    shutil.move(src, dst)
                    label = f"SPOSTATO -> {os.path.relpath(dst, self.folder_path)}"
                if has_cols:
                    self._mark_processed(cur, filename, label)
                moved += 1
            except Exception as e:
                errors.append(f"{filename}: {e}")
        conn.commit()
        conn.close()
        if not dry_run and moved:
            dest_txt = dest_rel or "Cestino di Windows"
            self._append_log(f"{action_prefix}: n={moved} err={len(errors)} dest={dest_txt}")
        return moved, errors

    def _fetch_filenames(self, where_clause, params=()):
        conn = self._connect()
        cur = conn.cursor()
        has_cols = self._has_output_cols(cur)
        filt = self._residue_filter(has_cols)
        cur.execute(f"SELECT filename FROM photo_reviews WHERE {where_clause} {filt} ORDER BY filename ASC", params)
        rows = [r[0] for r in cur.fetchall()]
        conn.close()
        return rows

    def count_by_decision(self, decision):
        st = self.get_statistics()
        if not st:
            return 0
        return {"KEEP": st["kept"], "DISCARD": st["discarded"],
                "PENDING": st["pending"], "ALTRO": st["altro"]}.get(decision, 0)

    # ----- Gruppo A: esaminate -----
    def trash_discards(self, dry_run=False):
        rows = self._fetch_filenames("decision = 'DISCARD'")
        return self._transfer(rows, dest_rel=None, mode="trash",
                              action_prefix="CESTINA DISCARD", dry_run=dry_run)

    def move_discards(self, target="scartate", dry_run=False):
        rows = self._fetch_filenames("decision = 'DISCARD'")
        return self._transfer(rows, dest_rel=target, mode="move",
                              action_prefix="SPOSTA DISCARD", dry_run=dry_run)

    def move_altro(self, target="ALTRO_da_verificare", dry_run=False):
        rows = self._fetch_filenames("decision = 'ALTRO'")
        return self._transfer(rows, dest_rel=target, mode="move",
                              action_prefix="ISOLA ALTRO", dry_run=dry_run)

    def archive_keep_by_year(self, dry_run=False):
        return self._archive_keep("year", dry_run)

    def archive_keep_by_year_month(self, dry_run=False):
        return self._archive_keep("month", dry_run)

    def _archive_keep(self, granularity="year", dry_run=False):
        conn = self._connect()
        cur = conn.cursor()
        has_cols = self._has_output_cols(cur)
        filt = self._residue_filter(has_cols)
        cur.execute(f"SELECT filename, effective_timestamp FROM photo_reviews WHERE decision='KEEP' {filt}")
        rows = cur.fetchall()
        conn.close()
        moved, errors = 0, []
        conn2 = self._connect()
        cur2 = conn2.cursor()
        has2 = self._has_output_cols(cur2)
        for filename, ts in rows:
            year, month = "Sconosciuto", None
            if ts and len(ts) >= 7 and ts[:4].isdigit():
                year = ts[:4]
                month = ts[5:7] if ts[5:7].isdigit() else None
            if granularity == "month":
                # v1.4.1: sottocartella leggibile "2023/gen 2023".
                if month and month in MESE_ITA:
                    dest_rel = os.path.join(year, f"{MESE_ITA[month]} {year}")
                else:
                    dest_rel = os.path.join(year, "SenzaMese")
            else:
                # Modalita solo-anno invariata: "2023".
                dest_rel = year
            src = os.path.join(self.folder_path, filename)
            if not os.path.exists(src):
                if has2 and not dry_run:
                    self._mark_processed(cur2, filename, "ARCHIVIA KEEP (assente)")
                continue
            try:
                if dry_run:
                    moved += 1
                    continue
                dest_abs = os.path.join(self.folder_path, dest_rel)
                os.makedirs(dest_abs, exist_ok=True)
                dst = self._resolve_collision(os.path.join(dest_abs, filename))
                shutil.move(src, dst)
                if has2:
                    rel = os.path.relpath(dst, self.folder_path)
                    self._mark_processed(cur2, filename, f"SPOSTATO -> {rel}")
                moved += 1
            except Exception as e:
                errors.append(f"{filename}: {e}")
        conn2.commit()
        conn2.close()
        if not dry_run and moved:
            self._append_log(f"ARCHIVIA KEEP ({granularity}): n={moved} err={len(errors)}")
        return moved, errors

    # ----- Gruppo B: PENDING -----
    def move_pending(self, target="Da_rivedere", dry_run=False):
        rows = self._fetch_filenames("decision = 'PENDING'")
        return self._transfer(rows, dest_rel=target, mode="move",
                              action_prefix="SPOSTA PENDING", dry_run=dry_run)

    def isolate_pending_nodate(self, target="Da_datare", dry_run=False):
        rows = self._fetch_filenames(
            "decision = 'PENDING' AND (effective_timestamp IS NULL "
            "OR effective_timestamp LIKE '1970%')")
        return self._transfer(rows, dest_rel=target, mode="move",
                              action_prefix="ISOLA PENDING senza data", dry_run=dry_run)

    def bulk_set_decision(self, to_decision, from_decision="PENDING"):
        """Rimarcatura massiva (non sposta nulla: output_action resta NULL)."""
        if to_decision not in ("KEEP", "DISCARD", "PENDING", "ALTRO"):
            return 0, [f"Decisione non valida: {to_decision}"]
        if from_decision not in ("KEEP", "DISCARD", "PENDING", "ALTRO"):
            return 0, [f"Filtro non valido: {from_decision}"]
        conn = self._connect()
        cur = conn.cursor()
        has_cols = self._has_output_cols(cur)
        filt = self._residue_filter(has_cols)
        cur.execute(f"SELECT count(*) FROM photo_reviews WHERE decision=? {filt}", (from_decision,))
        n = cur.fetchone()[0]
        try:
            if has_cols:
                cur.execute(
                    f"UPDATE photo_reviews SET decision=?, timestamp=? WHERE decision=? AND output_action IS NULL",
                    (to_decision, self._now(), from_decision))
            else:
                cur.execute("UPDATE photo_reviews SET decision=?, timestamp=? WHERE decision=?",
                            (to_decision, self._now(), from_decision))
            conn.commit()
        except Exception as e:
            conn.close()
            return 0, [str(e)]
        conn.close()
        self._append_log(f"RIMARCA {from_decision}->{to_decision}: n={n}")
        return n, []

    def reset_processed_flags(self):
        """Azzera marcature elaborate (se l'utente ha riportato file in radice)."""
        conn = self._connect()
        cur = conn.cursor()
        try:
            if not self._has_output_cols(cur):
                conn.close()
                return 0, ["Colonne output_action assenti (DB pre-1.4 non migrato)"]
            cur.execute("SELECT count(*) FROM photo_reviews WHERE output_action IS NOT NULL")
            n = cur.fetchone()[0]
            cur.execute("UPDATE photo_reviews SET output_action=NULL, output_timestamp=NULL WHERE output_action IS NOT NULL")
            conn.commit()
            conn.close()
            self._append_log(f"RESET marcature elaborate: n={n}")
            return n, []
        except Exception as e:
            conn.close()
            return 0, [str(e)]

    # ----- Gruppo C: SQL libera + export -----
    def validate_custom_sql(self, sql):
        import re as _re
        s = (sql or "").strip().rstrip(";").strip()
        if not s:
            return False, "Query vuota."
        low = s.lower()
        if not low.startswith("select"):
            return False, "Solo query SELECT."
        if "filename" not in low:
            return False, "La SELECT deve includere la colonna 'filename'."
        # Blocca stacking: piu' di un'istruzione.
        if ";" in s:
            return False, "Una sola istruzione SELECT (niente ';' interno)."
        for kw in FORBIDDEN_SQL_KEYWORDS:
            if _re.search(r"\b" + kw + r"\b", low):
                return False, f"Parola non consentita: {kw.upper()}"
        return True, ""

    def preview_custom_query(self, sql, limit=20):
        ok, msg = self.validate_custom_sql(sql)
        if not ok:
            return 0, [], [msg]
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            conn.close()
            names = [r[0] for r in rows if r and isinstance(r[0], str) and "." in r[0]]
            return len(names), names[:limit], []
        except Exception as e:
            return 0, [], [f"Errore SQL: {e}"]

    def preview_query_detailed(self, sql, limit=50):
        """Ritorna (total_count, sample_records, errors) dove ogni record e' un dict."""
        ok, msg = self.validate_custom_sql(sql)
        if not ok:
            return 0, [], [msg]
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            names = [r[0] for r in rows if r and isinstance(r[0], str) and "." in r[0]]
            records = []
            has_cols = self._has_output_cols(cur)
            for fname in names[:limit]:
                if has_cols:
                    cur.execute(
                        "SELECT filename, size_bytes, effective_timestamp, decision, output_action "
                        "FROM photo_reviews WHERE filename = ?", (fname,)
                    )
                else:
                    cur.execute(
                        "SELECT filename, size_bytes, effective_timestamp, decision "
                        "FROM photo_reviews WHERE filename = ?", (fname,)
                    )
                r = cur.fetchone()
                if r:
                    records.append({
                        "filename": r[0],
                        "size_bytes": r[1] if len(r) > 1 and r[1] else 0,
                        "effective_timestamp": r[2] if len(r) > 2 and r[2] else "Sconosciuta",
                        "decision": r[3] if len(r) > 3 and r[3] else "PENDING",
                        "output_action": r[4] if len(r) > 4 and r[4] else "Da elaborare"
                    })
                else:
                    records.append({"filename": fname, "size_bytes": 0, "effective_timestamp": "", "decision": "", "output_action": ""})
            conn.close()
            return len(names), records, []
        except Exception as e:
            return 0, [], [f"Errore SQL: {e}"]

    @staticmethod
    def open_path(path):
        """Apre un file o cartella nell'applicazione predefinita del sistema."""
        try:
            if os.path.exists(path):
                os.startfile(path)
                return True, ""
            return False, f"Percorso non trovato: {path}"
        except Exception as e:
            return False, str(e)

    def process_custom_query(self, sql_query, target_subfolder="Selezionati_da_Query", dry_run=False):
        """Esegue SELECT libera e sposta i file risultanti. Solo SELECT con filename."""
        ok, msg = self.validate_custom_sql(sql_query)
        if not ok:
            return 0, [msg]
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute(sql_query)
            rows = cur.fetchall()
            conn.close()
            names = [r[0] for r in rows if r and isinstance(r[0], str) and "." in r[0]]
        except Exception as e:
            return 0, [f"Errore SQL: {e}"]
        # Limita ai file ancora da elaborare (residuo): niente riprocessamenti.
        residuo = set(self._fetch_filenames("1=1"))
        names = [f for f in names if f in residuo]
        return self._transfer(names, dest_rel=target_subfolder, mode="move",
                              action_prefix="QUERY SQL", dry_run=dry_run)

    def export_csv(self, out_name="report_decisioni.csv"):
        out_path = os.path.join(self.folder_path, out_name)
        try:
            conn = self._connect()
            cur = conn.cursor()
            has_cols = self._has_output_cols(cur)
            try:
                cur.execute("PRAGMA table_info(photo_reviews)")
                all_cols = {r[1] for r in cur.fetchall()}
                has_sha = "sha256" in all_cols
                has_size = "size_bytes" in all_cols
            except Exception:
                has_sha, has_size = False, False
            extra_sel = ""
            extra_head = []
            if has_size:
                extra_sel += ", size_bytes"
                extra_head.append("size_bytes")
            if has_sha:
                extra_sel += ", sha256"
                extra_head.append("sha256")
            if has_cols:
                cur.execute("SELECT filename, decision, effective_timestamp, timestamp, output_action, output_timestamp" + extra_sel + " FROM photo_reviews ORDER BY effective_timestamp ASC, filename ASC")
            else:
                cur.execute("SELECT filename, decision, effective_timestamp, timestamp" + extra_sel + " FROM photo_reviews ORDER BY effective_timestamp ASC, filename ASC")
            rows = cur.fetchall()
            conn.close()
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if has_cols:
                    w.writerow(["filename", "decision", "effective_timestamp", "decision_timestamp", "output_action", "output_timestamp"] + extra_head)
                else:
                    w.writerow(["filename", "decision", "effective_timestamp", "decision_timestamp"] + extra_head)
                w.writerows(rows)
            self._append_log(f"EXPORT CSV: {out_name} righe={len(rows)}")
            # v1.5: export anche della tabella duplicati certi (audit separata).
            try:
                conn2 = self._connect()
                cur2 = conn2.cursor()
                cur2.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='duplicate_files'")
                if cur2.fetchone():
                    cur2.execute("SELECT filename, decision, effective_timestamp, timestamp, output_action, output_timestamp, size_bytes, sha256, original_keeper, moved_to FROM duplicate_files ORDER BY output_timestamp ASC, filename ASC")
                    drows = cur2.fetchall()
                    conn2.close()
                    dup_path = os.path.join(self.folder_path, "report_duplicati.csv")
                    with open(dup_path, "w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        w.writerow(["filename", "decision", "effective_timestamp", "decision_timestamp", "output_action", "output_timestamp", "size_bytes", "sha256", "original_keeper", "moved_to"])
                        w.writerows(drows)
                    self._append_log(f"EXPORT CSV: report_duplicati.csv righe={len(drows)}")
            except Exception:
                pass
            return out_path, []
        except Exception as e:
            return "", [str(e)]

    # ----- Wrapper retrocompatibili v1.3 -----
    def process_discards(self, target_subfolder="scartate"):
        return self.move_discards(target=target_subfolder)

    def process_altro(self, target_subfolder="ALTRO_da_verificare"):
        return self.move_altro(target=target_subfolder)

    def organize_keeps_by_year(self):
        return self.archive_keep_by_year()

