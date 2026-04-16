import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from retitle.api.tmdb import TMDBClient
from retitle.api.tvmaze import TVMazeClient
from retitle.renamer import MEDIA_EXTENSIONS, RenameProposal, Renamer


class RetitleApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Retitle - Media File Renamer")
        self.root.geometry("1000x600")
        self.root.minsize(800, 450)

        self.tvmaze = TVMazeClient()
        self.tmdb = None
        try:
            self.tmdb = TMDBClient()
        except ValueError:
            pass  # TMDB not configured, movie lookups disabled
        self.renamer = Renamer(self.tvmaze, self.tmdb)
        self.proposals: list[RenameProposal] = []

        self._build_ui()

    def _build_ui(self):
        # --- Top bar: path selection ---
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Path:").pack(side=tk.LEFT)

        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(top, textvariable=self.path_var)
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))

        ttk.Button(top, text="Browse File", command=self._browse_file).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(top, text="Browse Folder", command=self._browse_folder).pack(side=tk.LEFT)

        # --- Options bar ---
        opts = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        opts.pack(fill=tk.X)

        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Recursive (scan subfolders)", variable=self.recursive_var).pack(side=tk.LEFT)

        ttk.Button(opts, text="Scan", command=self._scan, style="Accent.TButton").pack(side=tk.RIGHT, padx=(8, 0))

        # --- Table ---
        table_frame = ttk.Frame(self.root, padding=(10, 0, 10, 0))
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("status", "original", "new_name")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("status", text="Status")
        self.tree.heading("original", text="Original Filename")
        self.tree.heading("new_name", text="New Filename")
        self.tree.column("status", width=80, minwidth=60, anchor=tk.CENTER)
        self.tree.column("original", width=400, minwidth=200)
        self.tree.column("new_name", width=400, minwidth=200)

        # Scrollbars
        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        # Tag colors
        self.tree.tag_configure("ready", foreground="#2e7d32")
        self.tree.tag_configure("conflict", foreground="#e65100")
        self.tree.tag_configure("no_match", foreground="#b71c1c")
        self.tree.tag_configure("error", foreground="#b71c1c")
        self.tree.tag_configure("skipped", foreground="#757575")

        # --- Bottom bar: actions + status ---
        bottom = ttk.Frame(self.root, padding=10)
        bottom.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="Select a file or folder to get started.")
        ttk.Label(bottom, textvariable=self.status_var, foreground="#555555").pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.rename_btn = ttk.Button(bottom, text="Rename Selected", command=self._rename_selected)
        self.rename_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self.rename_btn.state(["disabled"])

        self.rename_all_btn = ttk.Button(bottom, text="Rename All", command=self._rename_all)
        self.rename_all_btn.pack(side=tk.RIGHT)
        self.rename_all_btn.state(["disabled"])

    # --- Browse ---

    def _browse_file(self):
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTENSIONS))
        path = filedialog.askopenfilename(
            title="Select Media File",
            filetypes=[("Media files", exts), ("All files", "*.*")],
        )
        if path:
            self.path_var.set(path)
            self._scan()

    def _browse_folder(self):
        path = filedialog.askdirectory(title="Select Folder")
        if path:
            self.path_var.set(path)
            self._scan()

    # --- Scan ---

    def _scan(self):
        path_str = self.path_var.get().strip()
        if not path_str:
            messagebox.showwarning("No path", "Enter or browse to a file/folder first.")
            return

        target = Path(path_str)
        if not target.exists():
            messagebox.showerror("Not found", f"Path does not exist:\n{path_str}")
            return

        # Clear table
        self.tree.delete(*self.tree.get_children())
        self.proposals.clear()
        self.rename_btn.state(["disabled"])
        self.rename_all_btn.state(["disabled"])
        self.status_var.set("Scanning...")
        self.root.update_idletasks()

        # Run in background thread to keep UI responsive
        thread = threading.Thread(target=self._scan_worker, args=(target,), daemon=True)
        thread.start()

    def _scan_worker(self, target: Path):
        try:
            if target.is_file():
                if target.suffix.lower() not in MEDIA_EXTENSIONS:
                    self.root.after(0, lambda: self.status_var.set(f"Not a media file: {target.name}"))
                    return
                proposals = [self.renamer.propose_rename(target)]
            else:
                proposals = self.renamer.propose_batch(target, recursive=self.recursive_var.get())

            self.root.after(0, self._populate_table, proposals)
        except Exception as e:
            self.root.after(0, lambda: self.status_var.set(f"Error: {e}"))

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
                "", tk.END,
                iid=str(i),
                values=(status_text, p.original_path.name, new_name),
                tags=(p.status,),
            )

        ready_count = sum(1 for p in proposals if p.status == "ready")
        total = len(proposals)
        self.status_var.set(f"{total} file(s) scanned. {ready_count} ready to rename.")

        if ready_count > 0:
            self.rename_btn.state(["!disabled"])
            self.rename_all_btn.state(["!disabled"])

    # --- Rename ---

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
                to_rename.append((idx, p))

        if not to_rename:
            messagebox.showinfo("Nothing to rename", "None of the selected files are ready to rename.")
            return

        if not messagebox.askyesno("Confirm", f"Rename {len(to_rename)} file(s)?"):
            return

        self._execute_renames_indexed(to_rename)

    def _execute_renames(self, proposals: list[RenameProposal]):
        success = 0
        errors = []
        for p in proposals:
            try:
                if self.renamer.execute_rename(p):
                    success += 1
            except OSError as e:
                errors.append(f"{p.original_path.name}: {e}")

        self.status_var.set(f"Renamed {success}/{len(proposals)} file(s).")
        if errors:
            messagebox.showwarning("Some errors", "\n".join(errors))

        # Re-scan to update table
        self._scan()

    def _execute_renames_indexed(self, items: list[tuple[int, RenameProposal]]):
        success = 0
        errors = []
        for _idx, p in items:
            try:
                if self.renamer.execute_rename(p):
                    success += 1
            except OSError as e:
                errors.append(f"{p.original_path.name}: {e}")

        self.status_var.set(f"Renamed {success}/{len(items)} file(s).")
        if errors:
            messagebox.showwarning("Some errors", "\n".join(errors))

        self._scan()


def main():
    root = tk.Tk()
    RetitleApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
