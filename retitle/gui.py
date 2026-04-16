import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from retitle.api.opensubtitles import (
    OpenSubtitlesClient,
    SubtitleSearchResult,
    compute_hash,
)
from retitle.api.tmdb import TMDBClient
from retitle.api.tvmaze import TVMazeClient
from retitle.parser import parse_filename
from retitle.renamer import MEDIA_EXTENSIONS, RenameProposal, Renamer, SearchMatch

LANGUAGES = [
    ("Arabic", "ar"), ("Chinese", "zh"), ("Czech", "cs"),
    ("Dutch", "nl"), ("English", "en"), ("Finnish", "fi"),
    ("French", "fr"), ("German", "de"), ("Hebrew", "he"),
    ("Hungarian", "hu"), ("Italian", "it"), ("Japanese", "ja"),
    ("Korean", "ko"), ("Norwegian", "no"), ("Polish", "pl"),
    ("Portuguese", "pt"), ("Romanian", "ro"), ("Russian", "ru"),
    ("Spanish", "es"), ("Swedish", "sv"), ("Turkish", "tr"),
]

DEFAULT_LANG_INDEX = 4  # English


class MatchDialog(tk.Toplevel):
    """Dialog for choosing from API search results or editing GuessIt parse."""

    def __init__(self, parent, proposal: RenameProposal, renamer: Renamer):
        super().__init__(parent)
        self.proposal = proposal
        self.renamer = renamer
        self.result = None
        self.matches: list[SearchMatch] = []

        self.title(f"Choose Match \u2014 {proposal.original_path.name}")
        self.geometry("650x480")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        parsed = proposal.parsed

        # --- Parsed from filename (GuessIt) ---
        gi_frame = ttk.LabelFrame(self, text="Parsed from Filename (GuessIt)", padding=8)
        gi_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        row1 = ttk.Frame(gi_frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="Title:").pack(side=tk.LEFT)
        self.dlg_title_var = tk.StringVar(value=parsed.title or "")
        ttk.Entry(row1, textvariable=self.dlg_title_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8),
        )
        ttk.Label(row1, text="Type:").pack(side=tk.LEFT)
        self.dlg_type_var = tk.StringVar(value=parsed.media_type)
        ttk.Combobox(
            row1, textvariable=self.dlg_type_var,
            values=["episode", "movie"], state="readonly", width=8,
        ).pack(side=tk.LEFT, padx=(8, 0))

        row2 = ttk.Frame(gi_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="Season:").pack(side=tk.LEFT)
        self.dlg_season_var = tk.StringVar(
            value=str(parsed.season) if parsed.season is not None else "",
        )
        ttk.Entry(row2, textvariable=self.dlg_season_var, width=6).pack(
            side=tk.LEFT, padx=(8, 16),
        )
        ttk.Label(row2, text="Episode:").pack(side=tk.LEFT)
        if isinstance(parsed.episode, list):
            ep_display = ", ".join(str(e) for e in parsed.episode)
        else:
            ep_display = str(parsed.episode) if parsed.episode is not None else ""
        self.dlg_episode_var = tk.StringVar(value=ep_display)
        ttk.Entry(row2, textvariable=self.dlg_episode_var, width=8).pack(
            side=tk.LEFT, padx=(8, 16),
        )
        ttk.Label(row2, text="Year:").pack(side=tk.LEFT)
        self.dlg_year_var = tk.StringVar(
            value=str(parsed.year) if parsed.year is not None else "",
        )
        ttk.Entry(row2, textvariable=self.dlg_year_var, width=6).pack(
            side=tk.LEFT, padx=(8, 8),
        )
        ttk.Button(row2, text="Search", command=self._search).pack(side=tk.RIGHT)

        # --- Search results ---
        res_frame = ttk.LabelFrame(self, text="Search Results", padding=8)
        res_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        cols = ("source", "title", "info")
        self.match_tree = ttk.Treeview(
            res_frame, columns=cols, show="headings", selectmode="browse",
        )
        self.match_tree.heading("source", text="Source")
        self.match_tree.heading("title", text="Title")
        self.match_tree.heading("info", text="Info")
        self.match_tree.column("source", width=60, minwidth=50)
        self.match_tree.column("title", width=320, minwidth=150)
        self.match_tree.column("info", width=180, minwidth=80)

        vsb = ttk.Scrollbar(
            res_frame, orient=tk.VERTICAL, command=self.match_tree.yview,
        )
        self.match_tree.configure(yscrollcommand=vsb.set)
        self.match_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.match_tree.bind("<Double-1>", lambda _e: self._select_match())

        self.dlg_status_var = tk.StringVar(value="Searching...")
        ttk.Label(
            self, textvariable=self.dlg_status_var, foreground="#555555",
        ).pack(padx=10, anchor=tk.W)

        # --- Buttons ---
        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="Cancel", command=self._cancel).pack(
            side=tk.RIGHT, padx=(8, 0),
        )
        self.select_btn = ttk.Button(
            btn_frame, text="Use Selected", command=self._select_match,
        )
        self.select_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self.select_btn.state(["disabled"])
        ttk.Button(
            btn_frame, text="Use Parsed Only", command=self._use_parsed,
        ).pack(side=tk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._search()

    # --- field parsers ---

    def _parse_episode(self):
        val = self.dlg_episode_var.get().strip()
        if not val:
            return None
        parts = [p.strip() for p in val.replace("-", ",").split(",") if p.strip()]
        nums = []
        for p in parts:
            try:
                nums.append(int(p))
            except ValueError:
                pass
        if not nums:
            return None
        return nums[0] if len(nums) == 1 else nums

    def _parse_season(self):
        val = self.dlg_season_var.get().strip()
        try:
            return int(val) if val else None
        except ValueError:
            return None

    def _parse_year(self):
        val = self.dlg_year_var.get().strip()
        try:
            return int(val) if val else None
        except ValueError:
            return None

    # --- search ---

    def _search(self):
        title = self.dlg_title_var.get().strip()
        if not title:
            self.dlg_status_var.set("Enter a title to search.")
            return
        media_type = self.dlg_type_var.get()
        year = self._parse_year()
        self.match_tree.delete(*self.match_tree.get_children())
        self.matches.clear()
        self.dlg_status_var.set("Searching...")
        self.select_btn.state(["disabled"])
        threading.Thread(
            target=self._search_worker, args=(title, media_type, year), daemon=True,
        ).start()

    def _search_worker(self, title, media_type, year):
        try:
            if media_type == "episode":
                matches = self.renamer.search_tv_matches(title)
            else:
                matches = self.renamer.search_movie_matches(title, year)
            try:
                self.after(0, self._populate_matches, matches)
            except tk.TclError:
                pass
        except Exception as e:
            try:
                self.after(0, lambda: self.dlg_status_var.set(f"Error: {e}"))
            except tk.TclError:
                pass

    def _populate_matches(self, matches):
        self.matches = list(matches)
        self.match_tree.delete(*self.match_tree.get_children())
        for i, m in enumerate(matches):
            info_parts = []
            if m.year:
                info_parts.append(f"({m.year})")
            if m.extra_info:
                info_parts.append(m.extra_info)
            self.match_tree.insert(
                "", tk.END, iid=str(i),
                values=(m.source, m.title, " ".join(info_parts)),
            )
        if matches:
            self.match_tree.selection_set("0")
            self.select_btn.state(["!disabled"])
            self.dlg_status_var.set(
                f"{len(matches)} result(s). Select one and click 'Use Selected'.",
            )
        else:
            self.dlg_status_var.set("No results found.")

    # --- actions ---

    def _select_match(self):
        selected = self.match_tree.selection()
        if not selected:
            return
        idx = int(selected[0])
        self.result = {
            "choice": "match",
            "match": self.matches[idx],
            "media_type": self.dlg_type_var.get(),
            "season": self._parse_season(),
            "episode": self._parse_episode(),
            "year": self._parse_year(),
        }
        self.destroy()

    def _use_parsed(self):
        self.result = {
            "choice": "parsed",
            "match": None,
            "media_type": self.dlg_type_var.get(),
            "title": self.dlg_title_var.get().strip(),
            "season": self._parse_season(),
            "episode": self._parse_episode(),
            "year": self._parse_year(),
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class RetitleApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Retitle")
        self.root.geometry("1000x600")
        self.root.minsize(800, 450)

        # --- API clients ---
        self.tvmaze = TVMazeClient()
        self.tmdb = None
        try:
            self.tmdb = TMDBClient()
        except ValueError:
            pass
        self.renamer = Renamer(self.tvmaze, self.tmdb)

        self.opensubtitles: OpenSubtitlesClient | None = None
        try:
            self.opensubtitles = OpenSubtitlesClient()
        except ValueError:
            pass  # OpenSubtitles not configured

        # --- State ---
        self.proposals: list[RenameProposal] = []
        self.sub_results: list[SubtitleSearchResult] = []
        self._sub_file_path: Path | None = None

        self._build_ui()

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        rename_frame = ttk.Frame(notebook)
        notebook.add(rename_frame, text="  Rename  ")
        self._build_rename_tab(rename_frame)

        sub_frame = ttk.Frame(notebook)
        notebook.add(sub_frame, text="  Subtitles  ")
        self._build_subtitles_tab(sub_frame)

    # ================================================================
    #  RENAME TAB
    # ================================================================

    def _build_rename_tab(self, parent):
        # --- Top bar: path selection ---
        top = ttk.Frame(parent, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Path:").pack(side=tk.LEFT)
        self.rename_path_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.rename_path_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8),
        )
        ttk.Button(top, text="Browse File", command=self._rename_browse_file).pack(
            side=tk.LEFT, padx=(0, 4),
        )
        ttk.Button(top, text="Browse Folder", command=self._rename_browse_folder).pack(
            side=tk.LEFT,
        )

        # --- Options bar ---
        opts = ttk.Frame(parent, padding=(10, 0, 10, 8))
        opts.pack(fill=tk.X)

        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts, text="Recursive (scan subfolders)", variable=self.recursive_var,
        ).pack(side=tk.LEFT)
        self.parsed_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts, text="Parsed only (skip API)", variable=self.parsed_only_var,
        ).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Button(opts, text="Scan", command=self._scan).pack(
            side=tk.RIGHT, padx=(8, 0),
        )

        # --- Table ---
        table_frame = ttk.Frame(parent, padding=(10, 0, 10, 0))
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("status", "original", "new_name")
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="extended",
        )
        self.tree.heading("status", text="Status")
        self.tree.heading("original", text="Original Filename")
        self.tree.heading("new_name", text="New Filename")
        self.tree.column("status", width=80, minwidth=60, anchor=tk.CENTER)
        self.tree.column("original", width=400, minwidth=200)
        self.tree.column("new_name", width=400, minwidth=200)

        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.tree.tag_configure("ready", foreground="#2e7d32")
        self.tree.tag_configure("conflict", foreground="#e65100")
        self.tree.tag_configure("no_match", foreground="#b71c1c")
        self.tree.tag_configure("error", foreground="#b71c1c")
        self.tree.tag_configure("skipped", foreground="#757575")
        self.tree.bind("<Double-1>", self._on_rename_row_double_click)

        # --- Bottom bar ---
        bottom = ttk.Frame(parent, padding=10)
        bottom.pack(fill=tk.X)

        self.rename_status_var = tk.StringVar(value="Select a file or folder to get started.")
        ttk.Label(
            bottom, textvariable=self.rename_status_var, foreground="#555555",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.rename_btn = ttk.Button(
            bottom, text="Rename Selected", command=self._rename_selected,
        )
        self.rename_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self.rename_btn.state(["disabled"])

        self.rename_all_btn = ttk.Button(
            bottom, text="Rename All", command=self._rename_all,
        )
        self.rename_all_btn.pack(side=tk.RIGHT)
        self.rename_all_btn.state(["disabled"])

    # --- Rename: browse ---

    def _rename_browse_file(self):
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTENSIONS))
        path = filedialog.askopenfilename(
            title="Select Media File",
            filetypes=[("Media files", exts), ("All files", "*.*")],
        )
        if path:
            self.rename_path_var.set(path)

    def _rename_browse_folder(self):
        path = filedialog.askdirectory(title="Select Folder")
        if path:
            self.rename_path_var.set(path)

    # --- Rename: scan ---

    def _scan(self):
        path_str = self.rename_path_var.get().strip()
        if not path_str:
            messagebox.showwarning("No path", "Enter or browse to a file/folder first.")
            return

        target = Path(path_str)
        if not target.exists():
            messagebox.showerror("Not found", f"Path does not exist:\n{path_str}")
            return

        self.tree.delete(*self.tree.get_children())
        self.proposals.clear()
        self.rename_btn.state(["disabled"])
        self.rename_all_btn.state(["disabled"])
        self.rename_status_var.set("Scanning...")
        self.root.update_idletasks()

        if self.parsed_only_var.get():
            threading.Thread(
                target=self._scan_worker_parsed, args=(target,), daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._scan_parse_worker, args=(target,), daemon=True,
            ).start()

    def _scan_worker_parsed(self, target: Path):
        """Scan with parsed-only mode (no API calls, no dialogs)."""
        try:
            if target.is_file():
                if target.suffix.lower() not in MEDIA_EXTENSIONS:
                    self.root.after(
                        0, lambda: self.rename_status_var.set(
                            f"Not a media file: {target.name}",
                        ),
                    )
                    return
                proposals = [self.renamer.propose_rename(target, parsed_only=True)]
            else:
                proposals = self.renamer.propose_batch(
                    target, recursive=self.recursive_var.get(), parsed_only=True,
                )
            self.root.after(0, self._populate_table, proposals)
        except Exception as e:
            self.root.after(0, lambda: self.rename_status_var.set(f"Error: {e}"))

    def _scan_parse_worker(self, target: Path):
        """Parse filenames then hand off to main thread for match selection."""
        try:
            pairs = []
            if target.is_file():
                if target.suffix.lower() not in MEDIA_EXTENSIONS:
                    self.root.after(
                        0, lambda: self.rename_status_var.set(
                            f"Not a media file: {target.name}",
                        ),
                    )
                    return
                pairs.append((target, parse_filename(target.name)))
            else:
                recursive = self.recursive_var.get()
                files = sorted(
                    target.rglob("*") if recursive else target.iterdir(),
                )
                for f in files:
                    if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS:
                        pairs.append((f, parse_filename(f.name)))

            if not pairs:
                self.root.after(
                    0, lambda: self.rename_status_var.set("No media files found."),
                )
                return

            self.root.after(0, self._show_match_dialogs, pairs)
        except Exception as e:
            self.root.after(0, lambda: self.rename_status_var.set(f"Error: {e}"))

    def _show_match_dialogs(self, file_parsed_pairs):
        """Show a selection dialog per unique title, then generate proposals."""
        # Group files by (title, media_type) so each unique title gets one dialog
        groups = {}
        for filepath, parsed in file_parsed_pairs:
            if parsed.confidence == "low" or not parsed.title:
                key = None
            else:
                key = (parsed.title.lower(), parsed.media_type)
            groups.setdefault(key, []).append((filepath, parsed))

        self.rename_status_var.set(
            f"Parsed {len(file_parsed_pairs)} file(s). Choose matches...",
        )

        title_selections = {}
        for key, items in groups.items():
            if key is None:
                continue

            filepath, parsed = items[0]
            temp_proposal = RenameProposal(
                original_path=filepath,
                new_filename=None,
                new_path=None,
                parsed=parsed,
                api_result=None,
                status="no_match",
            )

            dialog = MatchDialog(self.root, temp_proposal, self.renamer)
            if len(items) > 1:
                dialog.title(
                    f"Choose Match \u2014 {parsed.title} ({len(items)} files)",
                )
            self.root.wait_window(dialog)

            if dialog.result is not None:
                title_selections[key] = dialog.result

        self.rename_status_var.set("Generating proposals...")
        self.root.update_idletasks()

        threading.Thread(
            target=self._generate_proposals_worker,
            args=(file_parsed_pairs, title_selections),
            daemon=True,
        ).start()

    def _generate_proposals_worker(self, file_parsed_pairs, title_selections):
        """Build proposals from user's match selections."""
        try:
            proposals = []
            for filepath, parsed in file_parsed_pairs:
                if parsed.confidence == "low" or not parsed.title:
                    proposals.append(RenameProposal(
                        original_path=filepath,
                        new_filename=None,
                        new_path=None,
                        parsed=parsed,
                        api_result=None,
                        status="no_match",
                        error_message="Could not identify media from filename",
                    ))
                    continue

                key = (parsed.title.lower(), parsed.media_type)
                choice = title_selections.get(key)

                if choice is None:
                    proposals.append(
                        self.renamer.propose_rename(filepath, parsed_only=True),
                    )
                elif choice["choice"] == "match":
                    match = choice["match"]
                    media_type = choice["media_type"]
                    season = parsed.season
                    episode = parsed.episode
                    if (
                        media_type == "episode"
                        and season is not None
                        and episode is not None
                    ):
                        proposals.append(self.renamer.propose_tv_with_match(
                            filepath, parsed, match, season, episode,
                        ))
                    else:
                        proposals.append(self.renamer.propose_movie_with_match(
                            filepath, parsed, match,
                        ))
                else:
                    proposals.append(self.renamer.propose_with_overrides(
                        filepath, parsed,
                        title=choice["title"],
                        media_type=choice["media_type"],
                        season=parsed.season,
                        episode=parsed.episode,
                        year=choice.get("year") or parsed.year,
                    ))

            self.root.after(0, self._populate_table, proposals)
        except Exception as e:
            self.root.after(
                0, lambda: self.rename_status_var.set(f"Error: {e}"),
            )

    def _populate_table(self, proposals: list[RenameProposal]):
        self.proposals = proposals
        self.tree.delete(*self.tree.get_children())

        for i, p in enumerate(proposals):
            status_text = {
                "ready": "\u2713 Ready",
                "conflict": "\u26a0 Conflict",
                "no_match": "\u2717 No match",
                "error": "\u2717 Error",
                "skipped": "- Skip",
            }.get(p.status, p.status)

            new_name = p.new_filename or p.error_message or ""
            self.tree.insert(
                "", tk.END, iid=str(i),
                values=(status_text, p.original_path.name, new_name),
                tags=(p.status,),
            )

        ready_count = sum(1 for p in proposals if p.status == "ready")
        total = len(proposals)
        self.rename_status_var.set(
            f"{total} file(s) scanned. {ready_count} ready to rename."
            " Double-click a row to change match."
        )

        if ready_count > 0:
            self.rename_btn.state(["!disabled"])
            self.rename_all_btn.state(["!disabled"])

    # --- Rename: execute ---

    def _rename_all(self):
        ready = [p for p in self.proposals if p.status == "ready"]
        if not ready:
            return
        if not messagebox.askyesno("Confirm", f"Rename {len(ready)} file(s)?"):
            return
        self._execute_renames(ready)

    def _rename_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("No selection", "Select rows to rename.")
            return

        to_rename = []
        for iid in selected:
            idx = int(iid)
            p = self.proposals[idx]
            if p.status == "ready":
                to_rename.append(p)

        if not to_rename:
            messagebox.showinfo("Nothing to rename", "None of the selected files are ready to rename.")
            return
        if not messagebox.askyesno("Confirm", f"Rename {len(to_rename)} file(s)?"):
            return
        self._execute_renames(to_rename)

    def _execute_renames(self, proposals: list[RenameProposal]):
        success = 0
        errors = []
        for p in proposals:
            try:
                if self.renamer.execute_rename(p):
                    success += 1
            except OSError as e:
                errors.append(f"{p.original_path.name}: {e}")

        self.rename_status_var.set(f"Renamed {success}/{len(proposals)} file(s).")
        if errors:
            messagebox.showwarning("Some errors", "\n".join(errors))
        self._refresh_after_rename()

    def _refresh_after_rename(self):
        """Re-scan with parsed-only to update table without re-showing dialogs."""
        path_str = self.rename_path_var.get().strip()
        if not path_str:
            return
        target = Path(path_str)
        if not target.exists():
            return
        self.tree.delete(*self.tree.get_children())
        self.proposals.clear()
        self.rename_btn.state(["disabled"])
        self.rename_all_btn.state(["disabled"])
        threading.Thread(
            target=self._scan_worker_parsed, args=(target,), daemon=True,
        ).start()

    # --- Rename: match selection ---

    def _on_rename_row_double_click(self, event):
        """Open match selection dialog on double-click."""
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        idx = int(row_id)
        if idx >= len(self.proposals):
            return

        proposal = self.proposals[idx]
        dialog = MatchDialog(self.root, proposal, self.renamer)
        self.root.wait_window(dialog)

        if dialog.result is None:
            return

        self.rename_status_var.set("Updating match...")
        self.root.update_idletasks()

        threading.Thread(
            target=self._apply_match_worker,
            args=(idx, dialog.result),
            daemon=True,
        ).start()

    def _apply_match_worker(self, idx, choice):
        """Generate new proposal based on user's match selection."""
        try:
            proposal = self.proposals[idx]
            filepath = proposal.original_path
            parsed = proposal.parsed

            if choice["choice"] == "match":
                match = choice["match"]
                media_type = choice["media_type"]
                season = choice["season"]
                episode = choice["episode"]

                if media_type == "episode" and season is not None and episode is not None:
                    new_proposal = self.renamer.propose_tv_with_match(
                        filepath, parsed, match, season, episode,
                    )
                else:
                    new_proposal = self.renamer.propose_movie_with_match(
                        filepath, parsed, match,
                    )
            else:
                new_proposal = self.renamer.propose_with_overrides(
                    filepath, parsed,
                    title=choice["title"],
                    media_type=choice["media_type"],
                    season=choice["season"],
                    episode=choice["episode"],
                    year=choice["year"],
                )

            self.root.after(0, self._update_row, idx, new_proposal)
        except Exception as e:
            self.root.after(
                0, lambda: self.rename_status_var.set(f"Error updating match: {e}"),
            )

    def _update_row(self, idx, new_proposal: RenameProposal):
        """Update a single row in the rename table after match selection."""
        self.proposals[idx] = new_proposal

        status_text = {
            "ready": "\u2713 Ready",
            "conflict": "\u26a0 Conflict",
            "no_match": "\u2717 No match",
            "error": "\u2717 Error",
            "skipped": "- Skip",
        }.get(new_proposal.status, new_proposal.status)

        new_name = new_proposal.new_filename or new_proposal.error_message or ""
        self.tree.item(
            str(idx),
            values=(status_text, new_proposal.original_path.name, new_name),
            tags=(new_proposal.status,),
        )

        ready_count = sum(1 for p in self.proposals if p.status == "ready")
        total = len(self.proposals)
        self.rename_status_var.set(
            f"{total} file(s) scanned. {ready_count} ready to rename."
            " Double-click a row to change match."
        )

        if ready_count > 0:
            self.rename_btn.state(["!disabled"])
            self.rename_all_btn.state(["!disabled"])
        else:
            self.rename_btn.state(["disabled"])
            self.rename_all_btn.state(["disabled"])

    # ================================================================
    #  SUBTITLES TAB
    # ================================================================

    def _build_subtitles_tab(self, parent):
        # --- File selection ---
        file_frame = ttk.Frame(parent, padding=(10, 10, 10, 4))
        file_frame.pack(fill=tk.X)

        ttk.Label(file_frame, text="File:").pack(side=tk.LEFT)
        self.sub_path_var = tk.StringVar()
        self.sub_path_var.trace_add("write", self._sub_on_path_changed)
        ttk.Entry(file_frame, textvariable=self.sub_path_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8),
        )
        ttk.Button(file_frame, text="Browse", command=self._sub_browse_file).pack(
            side=tk.LEFT,
        )

        # --- Row 1: Language + search buttons ---
        row1 = ttk.Frame(parent, padding=(10, 4, 10, 4))
        row1.pack(fill=tk.X)

        ttk.Label(row1, text="Subtitles language:").pack(side=tk.LEFT)
        self.sub_language_var = tk.StringVar(value="en")
        self._sub_lang_combo = ttk.Combobox(
            row1,
            textvariable=self.sub_language_var,
            values=[f"{name} ({code})" for name, code in LANGUAGES],
            state="readonly",
            width=18,
        )
        self._sub_lang_combo.current(DEFAULT_LANG_INDEX)
        self._sub_lang_combo.pack(side=tk.LEFT, padx=(8, 0))
        self._sub_lang_combo.bind("<<ComboboxSelected>>", self._sub_on_language_selected)

        self.search_name_btn = ttk.Button(
            row1, text="Search by name", command=self._sub_search_by_name,
        )
        self.search_name_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.search_hash_btn = ttk.Button(
            row1, text="Search by hash", command=self._sub_search_by_hash,
        )
        self.search_hash_btn.pack(side=tk.RIGHT)

        # --- Row 2: Title ---
        row2 = ttk.Frame(parent, padding=(10, 4, 10, 4))
        row2.pack(fill=tk.X)

        ttk.Label(row2, text="Title:", width=16, anchor=tk.E).pack(side=tk.LEFT)
        self.sub_title_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.sub_title_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0),
        )

        # --- Row 3: Season + Episode ---
        row3 = ttk.Frame(parent, padding=(10, 4, 10, 8))
        row3.pack(fill=tk.X)

        ttk.Label(row3, text="Season (series):", width=16, anchor=tk.E).pack(side=tk.LEFT)
        self.sub_season_var = tk.StringVar()
        ttk.Entry(row3, textvariable=self.sub_season_var, width=6).pack(
            side=tk.LEFT, padx=(8, 16),
        )

        ttk.Label(row3, text="Episode (series):").pack(side=tk.LEFT)
        self.sub_episode_var = tk.StringVar()
        ttk.Entry(row3, textvariable=self.sub_episode_var, width=6).pack(
            side=tk.LEFT, padx=(8, 0),
        )

        # --- Results table ---
        results_frame = ttk.Frame(parent, padding=(10, 0, 10, 0))
        results_frame.pack(fill=tk.BOTH, expand=True)

        sub_cols = ("release", "language", "downloads", "trusted")
        self.sub_tree = ttk.Treeview(
            results_frame, columns=sub_cols, show="headings", selectmode="browse",
        )
        self.sub_tree.heading("release", text="Release")
        self.sub_tree.heading("language", text="Lang")
        self.sub_tree.heading("downloads", text="Downloads")
        self.sub_tree.heading("trusted", text="Trusted")
        self.sub_tree.column("release", width=450, minwidth=200)
        self.sub_tree.column("language", width=50, minwidth=40, anchor=tk.CENTER)
        self.sub_tree.column("downloads", width=90, minwidth=60, anchor=tk.CENTER)
        self.sub_tree.column("trusted", width=60, minwidth=40, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=self.sub_tree.yview)
        self.sub_tree.configure(yscrollcommand=vsb.set)
        self.sub_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)

        # --- Bottom bar ---
        bottom = ttk.Frame(parent, padding=10)
        bottom.pack(fill=tk.X)

        self.sub_status_var = tk.StringVar(value="Select a media file to search for subtitles.")
        ttk.Label(
            bottom, textvariable=self.sub_status_var, foreground="#555555",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.sub_download_btn = ttk.Button(
            bottom, text="Download selection", command=self._sub_download,
        )
        self.sub_download_btn.pack(side=tk.RIGHT)
        self.sub_download_btn.state(["disabled"])

    # --- Subtitles: browse & parse ---

    def _sub_browse_file(self):
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTENSIONS))
        path = filedialog.askopenfilename(
            title="Select Media File",
            filetypes=[("Media files", exts), ("All files", "*.*")],
        )
        if path:
            self.sub_path_var.set(path)

    def _sub_on_path_changed(self, *_args):
        """Auto-parse filename when path changes and populate fields."""
        path_str = self.sub_path_var.get().strip()
        if not path_str:
            return

        filepath = Path(path_str)
        if not filepath.is_file():
            return

        self._sub_file_path = filepath
        parsed = parse_filename(filepath.name)

        self.sub_title_var.set(parsed.title or "")
        if parsed.season is not None:
            self.sub_season_var.set(str(parsed.season))
        else:
            self.sub_season_var.set("")
        if parsed.episode is not None:
            ep = parsed.episode[0] if isinstance(parsed.episode, list) else parsed.episode
            self.sub_episode_var.set(str(ep))
        else:
            self.sub_episode_var.set("")

    # --- Subtitles: language ---

    def _sub_on_language_selected(self, _event):
        selection = self.sub_language_var.get()
        for name, code in LANGUAGES:
            if selection == f"{name} ({code})":
                self.sub_language_var.set(code)
                break

    def _sub_get_language_code(self) -> str:
        val = self.sub_language_var.get()
        for name, code in LANGUAGES:
            if val == f"{name} ({code})":
                return code
        return val

    # --- Subtitles: search ---

    def _sub_check_configured(self) -> bool:
        if not self.opensubtitles:
            messagebox.showwarning(
                "Not configured",
                "OpenSubtitles not configured.\n"
                "Set OPENSUBTITLES_API_KEY, OPENSUBTITLES_USERNAME, and "
                "OPENSUBTITLES_PASSWORD in .env to enable subtitles.",
            )
            return False
        return True

    def _sub_search_by_name(self):
        if not self._sub_check_configured():
            return

        title = self.sub_title_var.get().strip()
        if not title:
            messagebox.showwarning("No title", "Enter a title to search for.")
            return

        season = self.sub_season_var.get().strip() or None
        episode = self.sub_episode_var.get().strip() or None
        lang = self._sub_get_language_code()

        self._sub_set_searching()

        thread = threading.Thread(
            target=self._sub_name_search_worker,
            args=(title, season, episode, lang),
            daemon=True,
        )
        thread.start()

    def _sub_search_by_hash(self):
        if not self._sub_check_configured():
            return

        path_str = self.sub_path_var.get().strip()
        if not path_str:
            messagebox.showwarning("No file", "Select a media file first.")
            return

        filepath = Path(path_str)
        if not filepath.is_file():
            messagebox.showerror("Not found", f"File does not exist:\n{path_str}")
            return

        lang = self._sub_get_language_code()
        self._sub_set_searching()

        thread = threading.Thread(
            target=self._sub_hash_search_worker,
            args=(filepath, lang),
            daemon=True,
        )
        thread.start()

    def _sub_set_searching(self):
        self.sub_tree.delete(*self.sub_tree.get_children())
        self.sub_results.clear()
        self.sub_download_btn.state(["disabled"])
        self.sub_status_var.set("Searching...")
        self.root.update_idletasks()

    def _sub_name_search_worker(self, title, season, episode, lang):
        try:
            season_num = int(season) if season else None
            episode_num = int(episode) if episode else None
            media_type = "episode" if season_num is not None else "movie"

            results = self.opensubtitles.search(
                query=title,
                season_number=season_num,
                episode_number=episode_num,
                languages=lang,
                media_type=media_type,
            )
            self.root.after(0, self._sub_populate_results, results)
        except Exception as e:
            self.root.after(0, lambda: self.sub_status_var.set(f"Search error: {e}"))

    def _sub_hash_search_worker(self, filepath, lang):
        try:
            moviehash, filesize = compute_hash(filepath)
            results = self.opensubtitles.search_by_hash(
                moviehash, filesize, languages=lang,
            )
            self.root.after(0, self._sub_populate_results, results)
        except ValueError as e:
            self.root.after(0, lambda: self.sub_status_var.set(f"Hash error: {e}"))
        except Exception as e:
            self.root.after(0, lambda: self.sub_status_var.set(f"Search error: {e}"))

    def _sub_populate_results(self, results: list[SubtitleSearchResult]):
        self.sub_results = results
        self.sub_tree.delete(*self.sub_tree.get_children())

        for i, r in enumerate(results):
            self.sub_tree.insert(
                "", tk.END, iid=str(i),
                values=(
                    r.release or "(unknown)",
                    r.language,
                    f"{r.download_count:,}",
                    "\u2713" if r.from_trusted else "",
                ),
            )

        count = len(results)
        if count > 0:
            self.sub_status_var.set(f"{count} subtitle(s) found.")
            self.sub_download_btn.state(["!disabled"])
        else:
            self.sub_status_var.set("No subtitles found.")
            self.sub_download_btn.state(["disabled"])

    # --- Subtitles: download ---

    def _sub_download(self):
        selected = self.sub_tree.selection()
        if not selected:
            messagebox.showinfo("No selection", "Select a subtitle from the results.")
            return

        idx = int(selected[0])
        result = self.sub_results[idx]

        # Determine save path
        file_path = self._sub_file_path
        if not file_path or not file_path.exists():
            path_str = self.sub_path_var.get().strip()
            if path_str:
                file_path = Path(path_str)

        if not file_path or not file_path.is_file():
            messagebox.showwarning(
                "No file",
                "Cannot determine where to save the subtitle.\n"
                "Make sure a media file is selected.",
            )
            return

        lang = self._sub_get_language_code()
        subtitle_path = file_path.with_suffix(f".{lang}.srt")

        if subtitle_path.exists():
            if not messagebox.askyesno(
                "Overwrite?",
                f"Subtitle already exists:\n{subtitle_path.name}\n\nOverwrite?",
            ):
                return

        self.sub_download_btn.state(["disabled"])
        self.sub_status_var.set("Downloading...")
        self.root.update_idletasks()

        thread = threading.Thread(
            target=self._sub_download_worker,
            args=(result.file_id, subtitle_path),
            daemon=True,
        )
        thread.start()

    def _sub_download_worker(self, file_id, subtitle_path):
        try:
            dl_result = self.opensubtitles.download(file_id)
            content = self.opensubtitles.download_content(dl_result.download_url)
            subtitle_path.write_bytes(content)

            remaining = dl_result.remaining
            self.root.after(
                0,
                lambda: self.sub_status_var.set(
                    f"Downloaded: {subtitle_path.name}  ({remaining} downloads remaining today)"
                ),
            )
        except ValueError as e:
            self.root.after(
                0, lambda: messagebox.showerror("Authentication required", str(e)),
            )
            self.root.after(0, lambda: self.sub_status_var.set("Download failed: authentication required."))
        except Exception as e:
            self.root.after(0, lambda: self.sub_status_var.set(f"Download error: {e}"))
        finally:
            self.root.after(0, lambda: self.sub_download_btn.state(["!disabled"]))


def main():
    root = tk.Tk()
    RetitleApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
