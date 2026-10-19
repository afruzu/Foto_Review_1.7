import hashlib
import os
import shutil
import sqlite3
import json
import re
import threading
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS

class DBAnalyzer:
    def __init__(self, folder_path):
        self.folder_path = folder_path
        self.db_path = os.path.join(self.folder_path, "photo_reviews.db")
        self.lock = threading.RLock()
        self.conn = None
        self.cursor = None
        self.valid_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.webp', '.heic')
        self.init_database()

    def init_database(self):
        with self.lock:
            self.conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            self.cursor = self.conn.cursor()
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS photo_reviews (
                    filename TEXT PRIMARY KEY,
                    size_bytes INTEGER,
                    sha256 TEXT DEFAULT NULL,
                    exif_data TEXT,
                    effective_timestamp TEXT,
                    decision TEXT DEFAULT 'PENDING',
                    timestamp DATETIME DEFAULT NULL
                )
            ''')
            self.conn.commit()
            # --- Migrazione v1.4: tracciabilita post-azione (output_action) ---
            # Rende auto-migranti i .db creati con v1.0-1.3 senza perdere decisioni.
            # --- Migrazione v1.5: checksum SHA-256 per deduplicazione certa ---
            # Rende auto-migranti i .db creati con v1.0-1.4 senza perdere decisioni.
            try:
                self.cursor.execute("PRAGMA table_info(photo_reviews)")
                existing_cols = {row[1] for row in self.cursor.fetchall()}
                if "output_action" not in existing_cols:
                    self.cursor.execute("ALTER TABLE photo_reviews ADD COLUMN output_action TEXT DEFAULT NULL")
                if "output_timestamp" not in existing_cols:
                    self.cursor.execute("ALTER TABLE photo_reviews ADD COLUMN output_timestamp TEXT DEFAULT NULL")
                if "sha256" not in existing_cols:
                    self.cursor.execute("ALTER TABLE photo_reviews ADD COLUMN sha256 TEXT DEFAULT NULL")
                if "timestamp" not in existing_cols:
                    self.cursor.execute("ALTER TABLE photo_reviews ADD COLUMN timestamp DATETIME DEFAULT NULL")
                self.conn.commit()
                # --- v1.5: tabella archivio duplicati certi ---
                # Struttura analoga a photo_reviews + metadati di spostamento.
                self.cursor.execute('''
                    CREATE TABLE IF NOT EXISTS duplicate_files (
                        filename TEXT PRIMARY KEY,
                        size_bytes INTEGER,
                        sha256 TEXT DEFAULT NULL,
                        exif_data TEXT,
                        effective_timestamp TEXT,
                        decision TEXT DEFAULT 'PENDING',
                        timestamp DATETIME DEFAULT NULL,
                        output_action TEXT DEFAULT NULL,
                        output_timestamp TEXT DEFAULT NULL,
                        original_keeper TEXT DEFAULT NULL,
                        moved_to TEXT DEFAULT NULL
                    )
                ''')
                self.conn.commit()
                # Indice per raggruppare rapidamente i duplicati (sha256 + dimensione).
                try:
                    self.cursor.execute(
                        "CREATE INDEX IF NOT EXISTS idx_photo_reviews_sha256_size "
                        "ON photo_reviews (sha256, size_bytes)"
                    )
                    self.conn.commit()
                except Exception:
                    pass
            except Exception:
                # Se la migrazione fallisce, il DB resta comunque utilizzabile (v1.3).
                try:
                    self.conn.commit()
                except Exception:
                    pass

    def _has_sha256_col(self):
        with self.lock:
            try:
                self.cursor.execute("PRAGMA table_info(photo_reviews)")
                cols = {row[1] for row in self.cursor.fetchall()}
                return "sha256" in cols
            except Exception:
                return False

    @staticmethod
    def compute_sha256(filepath, chunk_size=1024 * 1024):
        """Calcola lo SHA-256 del file a blocchi (sicuro anche per file grandi).

        Ritorna la stringa hexdigest in minuscolo, oppure None in caso di errore
        (file sparito, permessi, I/O)."""
        try:
            h = hashlib.sha256()
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None

    @staticmethod
    def parse_date_from_filename(filename):
        wa_match = re.search(r'(?:IMG|VID)[-_](\d{8})[-_]WA', filename, re.IGNORECASE)
        if wa_match:
            d = wa_match.group(1)
            return f"{d[:4]}-{d[4:6]}-{d[6:]} 00:00:00"

        dt_match = re.search(r'(\d{8})_(\d{6})', filename)
        if dt_match:
            d, t = dt_match.groups()
            return f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}:{t[4:6]}"

        date_match = re.search(r'(?:19|20)\d{2}[0-1]\d[0-3]\d', filename)
        if date_match:
            d = date_match.group(0)
            return f"{d[:4]}-{d[4:6]}-{d[6:]} 00:00:00"

        return None

    @staticmethod
    def extract_exif(filepath):
        """Estrae i metadati EXIF verificando l'integrità del file immagine."""
        exif_summary = {}
        try:
            with Image.open(filepath) as img:
                img.verify() # Controllo rapido integrità file
            
            with Image.open(filepath) as img:
                exif_raw = None
                if hasattr(img, "getexif"):
                    try:
                        exif_raw = img.getexif()
                    except Exception:
                        exif_raw = None
                
                if not exif_raw and hasattr(img, "_getexif"):
                    try:
                        exif_raw = img._getexif()
                    except Exception:
                        exif_raw = None

                if exif_raw:
                    for tag_id, value in exif_raw.items():
                        tag = TAGS.get(tag_id, tag_id)
                        if isinstance(value, bytes):
                            try:
                                value = value.decode('utf-8', errors='ignore')
                            except Exception:
                                value = str(value)
                        exif_summary[str(tag)] = str(value)
        except Exception:
            raise ValueError("File immagine non valido o corrotto")
        return exif_summary

    @staticmethod
    def _worker_analyze_file(args):
        """Funzione worker per l'analisi parallela di un file (I/O, EXIF e SHA-256)."""
        folder_path, filename, has_sha, needs_full = args
        filepath = os.path.join(folder_path, filename)
        try:
            size_bytes = os.path.getsize(filepath)
        except Exception:
            size_bytes = 0

        if not needs_full:
            sha256_val = DBAnalyzer.compute_sha256(filepath) if (has_sha and size_bytes > 0) else None
            return ("UPDATE", filename, size_bytes, sha256_val, None, None, None)

        is_anomaly = False
        exif_dict = {}
        if size_bytes == 0:
            is_anomaly = True
        else:
            try:
                exif_dict = DBAnalyzer.extract_exif(filepath)
            except Exception:
                is_anomaly = True

        exif_json = json.dumps(exif_dict, ensure_ascii=False)
        sha256_val = DBAnalyzer.compute_sha256(filepath) if (has_sha and size_bytes > 0) else None

        if is_anomaly:
            effective_ts = "1970-01-01 00:00:00"
            initial_decision = "ALTRO"
        else:
            effective_ts = None
            for tag_name in ['DateTimeOriginal', 'DateTime', 'DateTimeDigitized']:
                if tag_name in exif_dict:
                    val = exif_dict[tag_name]
                    cleaned = val.replace(':', '-', 2)
                    if len(cleaned) >= 19:
                        effective_ts = cleaned[:19]
                        break

            if not effective_ts:
                effective_ts = DBAnalyzer.parse_date_from_filename(filename)

            if not effective_ts:
                try:
                    mtime = os.path.getmtime(filepath)
                    effective_ts = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    effective_ts = "1970-01-01 00:00:00"

            initial_decision = "PENDING"

        return ("INSERT", filename, size_bytes, sha256_val, exif_json, effective_ts, initial_decision)


    def _apply_batch_inserts(self, batch_inserts, has_sha):
        with self.lock:
            if not batch_inserts:
                return
            if has_sha:
                self.cursor.executemany(
                    "INSERT OR IGNORE INTO photo_reviews "
                    "(filename, size_bytes, sha256, exif_data, effective_timestamp, decision, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    batch_inserts
                )
            else:
                simple_inserts = [(r[0], r[1], r[3], r[4], r[5]) for r in batch_inserts]
                self.cursor.executemany(
                    "INSERT OR IGNORE INTO photo_reviews "
                    "(filename, size_bytes, exif_data, effective_timestamp, decision, timestamp) "
                    "VALUES (?, ?, ?, ?, NULL)",
                    simple_inserts
                )
            self.conn.commit()

    def _apply_batch_updates(self, batch_updates):
        with self.lock:
            if not batch_updates:
                return
            self.cursor.executemany(
                "UPDATE photo_reviews SET sha256 = ?, size_bytes = ? WHERE filename = ?",
                batch_updates
            )
            self.conn.commit()

    def sync_files_to_db_multithread(self, valid_files, progress_callback=None, max_workers=None):
        """Sincronizzazione multithread ad alte prestazioni con callback di progresso live."""
        import concurrent.futures
        has_sha = self._has_sha256_col()
        if max_workers is None:
            max_workers = min(12, max(2, (os.cpu_count() or 4)))

        # 1. Recupero rapido in blocco dei record già esistenti (singola query)
        with self.lock:
            if has_sha:
                try:
                    self.cursor.execute("SELECT filename, sha256, size_bytes FROM photo_reviews")
                    existing = {r[0]: (r[1], r[2]) for r in self.cursor.fetchall()}
                except Exception:
                    has_sha = False
                    self.cursor.execute("SELECT filename FROM photo_reviews")
                    existing = {r[0]: (None, None) for r in self.cursor.fetchall()}
            else:
                self.cursor.execute("SELECT filename FROM photo_reviews")
                existing = {r[0]: (None, None) for r in self.cursor.fetchall()}

        # 2. Suddivisione dei compiti
        tasks = []
        already_synced = 0
        total_files = len(valid_files)

        for filename in valid_files:
            filepath = os.path.join(self.folder_path, filename)
            try:
                cur_size = os.path.getsize(filepath)
            except Exception:
                cur_size = 0

            if filename in existing:
                ex_sha, ex_size = existing[filename]
                needs_hash = (has_sha and ex_sha is None and cur_size > 0) or (
                    ex_size is not None and ex_size != cur_size and cur_size > 0
                )
                if needs_hash:
                    tasks.append((self.folder_path, filename, has_sha, False))
                else:
                    already_synced += 1
            else:
                tasks.append((self.folder_path, filename, has_sha, True))

        completed_count = already_synced
        if progress_callback:
            progress_callback(completed_count, total_files, "Controllo file esistenti...")

        if not tasks:
            if progress_callback:
                progress_callback(total_files, total_files, "Completato")
            return

        # 3. Esecuzione parallela tramite ThreadPoolExecutor
        batch_inserts = []
        batch_updates = []
        batch_size = 50

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_fn = {executor.submit(DBAnalyzer._worker_analyze_file, t): t[1] for t in tasks}

            for future in concurrent.futures.as_completed(future_to_fn):
                fn = future_to_fn[future]
                try:
                    res = future.result()
                    action = res[0]
                    if action == "INSERT":
                        batch_inserts.append(res[1:])
                    elif action == "UPDATE":
                        batch_updates.append((res[3], res[2], res[1]))
                except Exception:
                    pass

                completed_count += 1
                if progress_callback and (completed_count % 5 == 0 or completed_count == total_files):
                    progress_callback(completed_count, total_files, fn)

                # Commit a blocchi
                if len(batch_inserts) >= batch_size:
                    self._apply_batch_inserts(batch_inserts, has_sha)
                    batch_inserts.clear()
                if len(batch_updates) >= batch_size:
                    self._apply_batch_updates(batch_updates)
                    batch_updates.clear()

        # Commit finale residui
        if batch_inserts:
            self._apply_batch_inserts(batch_inserts, has_sha)
        if batch_updates:
            self._apply_batch_updates(batch_updates)

        with self.lock:
            self.conn.commit()
        if progress_callback:
            progress_callback(total_files, total_files, "Scansione terminata con successo")

    def sync_files_to_db(self, valid_files):
        """Wrapper retrocompatibile: esegue la sincronizzazione multithread senza callback."""
        self.sync_files_to_db_multithread(valid_files)

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

    def _append_dedup_log(self, message):
        try:
            log_path = os.path.join(self.folder_path, "output_log.txt")
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{now_str}] {message}\n")
        except Exception:
            pass

    def deduplicate_exact_copies(self, target_subfolder="duplicati certi"):
        """v1.5: sposta i duplicati certi (stesso SHA-256 + stessa dimensione).

        Tiene un solo file in radice per gruppo e sposta gli altri in
        <folder>/duplicati certi/. Solo PENDING residenti, sha NOT NULL,
        size > 0. Il record viene SPOSTATO da photo_reviews a duplicate_files
        (tabella analoga + keeper/destinazione/timestamp): la tabella
        principale resta allineata alla cartella sottoposta a esame visivo.
        Ritorna (moved_count, errors_list)."""
        errors = []
        moved = 0
        if not self._has_sha256_col():
            return 0, []
        with self.lock:
            try:
                try:
                    self.cursor.execute("PRAGMA table_info(photo_reviews)")
                    cols = {row[1] for row in self.cursor.fetchall()}
                    has_output = "output_action" in cols and "output_timestamp" in cols
                except Exception:
                    has_output = False
                try:
                    self.cursor.execute(
                        "SELECT filename FROM photo_reviews WHERE sha256 IS NULL"
                        + (" AND output_action IS NULL" if has_output else "")
                    )
                    for (fname,) in self.cursor.fetchall():
                        fp = os.path.join(self.folder_path, fname)
                        try:
                            if os.path.isfile(fp) and os.path.getsize(fp) > 0:
                                h = self.compute_sha256(fp)
                                if h:
                                    self.cursor.execute(
                                        "UPDATE photo_reviews SET sha256 = ? WHERE filename = ?",
                                        (h, fname),
                                    )
                        except Exception:
                            pass
                    self.conn.commit()
                except Exception:
                    pass
                filt = "AND output_action IS NULL" if has_output else ""
                self.cursor.execute(
                    "SELECT sha256, size_bytes FROM photo_reviews "
                    "WHERE decision = 'PENDING' AND sha256 IS NOT NULL "
                    f"AND size_bytes > 0 {filt} "
                    "GROUP BY sha256, size_bytes HAVING COUNT(*) > 1"
                )
                dup_groups = self.cursor.fetchall()
                if not dup_groups:
                    return 0, []
                dest_abs = os.path.join(self.folder_path, target_subfolder)
                try:
                    os.makedirs(dest_abs, exist_ok=True)
                except Exception as e:
                    return 0, [f"Impossibile creare '{target_subfolder}/': {e}"]
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for sha_val, size_val in dup_groups:
                    try:
                        self.cursor.execute(
                            "SELECT filename FROM photo_reviews "
                            "WHERE decision = 'PENDING' AND sha256 = ? AND size_bytes = ? "
                            f"{filt} ORDER BY effective_timestamp ASC, filename ASC",
                            (sha_val, size_val),
                        )
                        group_files = [r[0] for r in self.cursor.fetchall()]
                    except Exception as e:
                        errors.append(f"gruppo sha={str(sha_val)[:12]}: {e}")
                        continue
                    if len(group_files) < 2:
                        continue
                    keeper = None
                    for fname in group_files:
                        if os.path.isfile(os.path.join(self.folder_path, fname)):
                            keeper = fname
                            break
                    if keeper is None:
                        continue
                    for fname in group_files:
                        if fname == keeper:
                            continue
                        src = os.path.join(self.folder_path, fname)
                        if not os.path.isfile(src):
                            continue
                        try:
                            # Legge il record completo prima dello spostamento fisico.
                            try:
                                self.cursor.execute(
                                    "SELECT filename, size_bytes, sha256, exif_data, "
                                    "effective_timestamp, decision, timestamp "
                                    "FROM photo_reviews WHERE filename = ?",
                                    (fname,),
                                )
                                rec = self.cursor.fetchone()
                            except Exception:
                                rec = None
                            dst = self._resolve_collision(os.path.join(dest_abs, fname))
                            shutil.move(src, dst)
                            rel = os.path.relpath(dst, self.folder_path)
                            action_label = f"DUPLICATO CERTO -> {rel} (orig: {keeper})"
                            if rec:
                                try:
                                    self.cursor.execute(
                                        "INSERT OR REPLACE INTO duplicate_files "
                                        "(filename, size_bytes, sha256, exif_data, "
                                        "effective_timestamp, decision, timestamp, "
                                        "output_action, output_timestamp, "
                                        "original_keeper, moved_to) "
                                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                        (rec[0], rec[1], rec[2], rec[3], rec[4], rec[5],
                                         rec[6], action_label, now_str, keeper, rel),
                                    )
                                except Exception:
                                    pass
                            try:
                                self.cursor.execute(
                                    "DELETE FROM photo_reviews WHERE filename = ?",
                                    (fname,),
                                )
                            except Exception:
                                pass
                            moved += 1
                        except Exception as e:
                            errors.append(f"{fname}: {e}")
                try:
                    self.conn.commit()
                except Exception:
                    pass
                if moved:
                    self._append_dedup_log(
                        f"DEDUPLICA v1.5: n={moved} err={len(errors)} dest={target_subfolder}/ "
                        f"(stesso SHA-256 + stessa dimensione; record spostati in duplicate_files)"
                    )
                return moved, errors
            except Exception as e:
                try:
                    self.conn.commit()
                except Exception:
                    pass
                return moved, errors + [str(e)]

    def get_duplicate_files(self):
        """Ritorna i record archiviati in duplicate_files (audit duplicati certi)."""
        with self.lock:
            try:
                self.cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='duplicate_files'"
                )
                if not self.cursor.fetchone():
                    return []
                self.cursor.execute(
                    "SELECT filename, size_bytes, sha256, effective_timestamp, "
                    "original_keeper, moved_to, output_timestamp "
                    "FROM duplicate_files ORDER BY output_timestamp ASC, filename ASC"
                )
                return self.cursor.fetchall()
            except Exception:
                return []

    def get_sorted_files(self, include_processed=False):
        """Restituisce i file da revisionare in ordine cronologico.
        Di default (v1.4) esclude i file gia' elaborati (output_action NOT NULL)
        perche' non piu' presenti nell'archivio originale.
        Con include_processed=True restituisce tutto (uso audit)."""
        with self.lock:
            try:
                if include_processed:
                    self.cursor.execute("SELECT filename FROM photo_reviews ORDER BY effective_timestamp ASC, filename ASC")
                else:
                    # Colonne v1.4 potrebbero mancare se migrazione fallita: fallback v1.3.
                    self.cursor.execute("PRAGMA table_info(photo_reviews)")
                    cols = {row[1] for row in self.cursor.fetchall()}
                    if "output_action" in cols:
                        self.cursor.execute(
                            "SELECT filename FROM photo_reviews WHERE output_action IS NULL "
                            "ORDER BY effective_timestamp ASC, filename ASC"
                        )
                    else:
                        self.cursor.execute("SELECT filename FROM photo_reviews ORDER BY effective_timestamp ASC, filename ASC")
                return [row[0] for row in self.cursor.fetchall()]
            except Exception:
                try:
                    self.cursor.execute("SELECT filename FROM photo_reviews ORDER BY effective_timestamp ASC, filename ASC")
                    return [row[0] for row in self.cursor.fetchall()]
                except Exception:
                    return []

    def get_file_record(self, filename):
        with self.lock:
            try:
                self.cursor.execute("PRAGMA table_info(photo_reviews)")
                cols = {row[1] for row in self.cursor.fetchall()}
                has_out = "output_action" in cols and "output_timestamp" in cols
                has_sha = "sha256" in cols
                sel_extra = ""
                if has_sha:
                    sel_extra += ", sha256, size_bytes"
                if has_out:
                    self.cursor.execute(
                        "SELECT decision, exif_data, effective_timestamp, output_action, output_timestamp"
                        f"{sel_extra} FROM photo_reviews WHERE filename = ?", (filename,)
                    )
                else:
                    self.cursor.execute(
                        f"SELECT decision, exif_data, effective_timestamp{sel_extra} "
                        "FROM photo_reviews WHERE filename = ?", (filename,)
                    )
                return self.cursor.fetchone()
            except Exception:
                try:
                    self.cursor.execute("SELECT decision, exif_data, effective_timestamp FROM photo_reviews WHERE filename = ?", (filename,))
                    return self.cursor.fetchone()
                except Exception:
                    return None

    def get_sha_size(self, filename):
        """Ritorna (sha256, size_bytes) per il pannello info; (None, None) se assenti."""
        with self.lock:
            try:
                self.cursor.execute("PRAGMA table_info(photo_reviews)")
                cols = {row[1] for row in self.cursor.fetchall()}
                if "sha256" not in cols:
                    return None, None
                self.cursor.execute(
                    "SELECT sha256, size_bytes FROM photo_reviews WHERE filename = ?", (filename,)
                )
                r = self.cursor.fetchone()
                if not r:
                    return None, None
                return r[0], r[1] if len(r) > 1 else None
            except Exception:
                return None, None

    def update_decision(self, filename, decision):
        with self.lock:
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.cursor.execute('''
                UPDATE photo_reviews 
                SET decision = ?, timestamp = ? 
                WHERE filename = ?
            ''', (decision, now_str, filename))
            self.conn.commit()

    def get_duplicate_count(self):
        """Ritorna il conteggio dei file archiviati nella tabella duplicate_files."""
        with self.lock:
            try:
                self.cursor.execute("SELECT count(*) FROM duplicate_files")
                return self.cursor.fetchone()[0]
            except Exception:
                return 0

    def get_quick_stats(self):
        """Ritorna un riepilogo rapido dei conteggi per la UI principale."""
        with self.lock:
            try:
                self.cursor.execute("PRAGMA table_info(photo_reviews)")
                cols = {row[1] for row in self.cursor.fetchall()}
                has_out = "output_action" in cols
                filt = "WHERE output_action IS NULL" if has_out else ""
                self.cursor.execute(f"SELECT decision, count(*) FROM photo_reviews {filt} GROUP BY decision")
                counts = {row[0]: row[1] for row in self.cursor.fetchall()}
                self.cursor.execute(f"SELECT count(*) FROM photo_reviews {filt}")
                tot_residue = self.cursor.fetchone()[0]
                self.cursor.execute("SELECT count(*) FROM photo_reviews")
                tot_overall = self.cursor.fetchone()[0]
                return {
                    "total": tot_overall,
                    "residue": tot_residue,
                    "keep": counts.get("KEEP", 0),
                    "discard": counts.get("DISCARD", 0),
                    "pending": counts.get("PENDING", 0),
                    "altro": counts.get("ALTRO", 0),
                    "duplicates": self.get_duplicate_count()
                }
            except Exception:
                return {
                    "total": 0, "residue": 0, "keep": 0, "discard": 0,
                    "pending": 0, "altro": 0, "duplicates": 0
                }

    def close(self):
        with self.lock:
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
                self.cursor = None