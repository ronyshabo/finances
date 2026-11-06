"""Connection to a running GridCraft window over its JSON-lines control channel.

Wire format (one JSON object per line, both ways):
    -> {"id": 1, "method": "sheet.read", "params": {...}}
    <- {"id": 1, "ok": true, "result": {...}}   or   {"id": 1, "ok": false, "error": "..."}

GridCraft is installed as a sandboxed Flatpak without network access, so the
control port is only reachable from here when the app is launched with
`--share=network`. `ensure_running()` takes care of that.
"""
import json
import socket
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

EXCEL_EPOCH = date(1899, 12, 30)


class GridCraftError(RuntimeError):
    pass


def serial_to_date(v):
    """Spreadsheet date serial (or ISO text) -> date; anything else -> None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return EXCEL_EPOCH + timedelta(days=int(v))
    if isinstance(v, str) and v:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(v.strip(), fmt).date()
            except ValueError:
                pass
    return None


def col_letter(n):
    """0-based column index -> letters (0 -> A, 26 -> AA)."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class GridCraft:
    def __init__(self, port=7979, app_id="ai.storyteller.gridcraft", timeout=15):
        self.port = port
        self.app_id = app_id
        self.timeout = timeout
        self._sock = None
        self._file = None
        self._next_id = 0

    # ---- connection -----------------------------------------------------
    @property
    def connected(self):
        return self._sock is not None

    def _port_open(self):
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                return True
        except OSError:
            return False

    def connect(self):
        self.close()
        self._sock = socket.create_connection(("127.0.0.1", self.port), timeout=self.timeout)
        self._file = self._sock.makefile("rwb")

    def close(self):
        for obj in (self._file, self._sock):
            try:
                if obj:
                    obj.close()
            except OSError:
                pass
        self._sock = self._file = None

    def launch(self, workbook_path=None):
        """Start a GridCraft window with the control channel enabled."""
        cmd = ["flatpak", "run", "--share=network", "--command=gridcraft", self.app_id,
               "--control", str(self.port)]
        if workbook_path and Path(workbook_path).exists():
            cmd.append(str(workbook_path))
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    def ensure_running(self, workbook_path=None, wait_s=30):
        """Connect to GridCraft on our port, launching it first if needed.

        Returns True when GridCraft was launched by this call (so the
        workbook was opened from the command line).
        """
        launched = False
        if not self._port_open():
            self.launch(workbook_path)
            launched = True
            deadline = time.time() + wait_s
            while not self._port_open():
                if time.time() > deadline:
                    raise GridCraftError(
                        f"GridCraft did not open its control port {self.port} within {wait_s}s")
                time.sleep(0.3)
        self.connect()
        return launched

    # ---- commands -------------------------------------------------------
    def call(self, method, params=None):
        if not self._file:
            raise GridCraftError("Not connected to GridCraft")
        self._next_id += 1
        msg = {"id": self._next_id, "method": method, "params": params or {}}
        try:
            self._file.write((json.dumps(msg) + "\n").encode())
            self._file.flush()
            while True:
                line = self._file.readline()
                if not line:
                    raise GridCraftError("GridCraft closed the connection")
                reply = json.loads(line)
                if reply.get("id") == msg["id"]:
                    break
        except (OSError, ValueError) as e:
            self.close()
            raise GridCraftError(f"Lost connection to GridCraft: {e}") from e
        if not reply.get("ok", "error" not in reply):
            raise GridCraftError(f"{method}: {reply.get('error')}")
        return reply.get("result")

    def try_call(self, method, params=None):
        try:
            return self.call(method, params)
        except GridCraftError:
            return None

    # ---- workbook helpers ----------------------------------------------
    def sheet_exists(self, name):
        try:
            self.call("sheet.read", {"sheet": name, "range": "A1"})
            return True
        except GridCraftError as e:
            if "no sheet named" in str(e):
                return False
            raise

    def ensure_sheet(self, name, headers):
        """Create the sheet with a bold header row if it doesn't exist yet."""
        if self.sheet_exists(name):
            return False
        self.call("home.insertSheet", {"name": name})
        self.call("sheet.activate", {"sheet": name})
        self.call("range.setValues", {"range": "A1", "values": [headers]})
        self.try_call("home.formatCells", {"range": f"A1:{col_letter(len(headers) - 1)}1",
                                           "style": {"font": {"bold": True}}})
        return True

    def read_sheet(self, name):
        """All used cells of a sheet as a list of rows (row 0 = headers)."""
        res = self.call("sheet.read", {"sheet": name})
        return (res or {}).get("values") or []

    def write_rows(self, sheet, start_row, rows):
        """Write rows (lists) starting at 1-based row `start_row`, column A."""
        if not rows:
            return
        self.call("sheet.activate", {"sheet": sheet})
        self.call("range.setValues", {"range": f"A{start_row}", "values": rows})

    def clear_sheet_below_header(self, sheet, ncols):
        rows = self.read_sheet(sheet)
        if len(rows) > 1:
            self.call("sheet.activate", {"sheet": sheet})
            self.call("edit.clearContents", {"range": f"A2:{col_letter(max(ncols, len(rows[0])) - 1)}{len(rows)}"})

    def open(self, path):
        return self.call("file.open", {"path": str(path)})

    def save(self, path=None):
        return self.call("file.saveAs", {"path": str(path)}) if path else self.call("file.save", {})
