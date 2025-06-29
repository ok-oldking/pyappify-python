# pyappify/__init__.py
import os
import signal
import hashlib
import shutil
import urllib.request
import zipfile
import threading

app_version = os.environ.get("PYAPPIFY_APP_VERSION")
app_profile = os.environ.get("PYAPPIFY_APP_PROFILE")
pyappify_version = os.environ.get("PYAPPIFY_VERSION")
pyappify_executable = os.environ.get("PYAPPIFY_EXECUTABLE")

pyappify_upgradeable = os.environ.get("PYAPPIFY_UPGRADEABLE") == '1'
logger = None

try:
    pid = int(os.environ.get("PYAPPIFY_PID"))
except (ValueError, TypeError):
    pid = None

import sys

try:
    import ctypes
except ImportError:
    ctypes = None


def minimize_window_by_pid(pid):
    if not ctypes or sys.platform != "win32":
        return False

    found_hwnd = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))

    def enum_windows_callback(hwnd, lParam):
        owner_pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and ctypes.windll.user32.IsWindowVisible(hwnd):
            found_hwnd.append(hwnd)
            return False
        return True

    ctypes.windll.user32.EnumWindows(EnumWindowsProc(enum_windows_callback), 0)

    if found_hwnd:
        ctypes.windll.user32.ShowWindow(found_hwnd[0], 6)
        return True

    return False

def kill_pyappify():
    if pid:
        if logger:
            logger.info(f"Attempting to terminate process with PID: {pid}")
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception as e:
            if logger:
                logger.error(f"Failed to terminate process with PID {pid}: {e}")
            pass

def hide_pyappify():
    if pid:
        if logger:
            logger.info(f"Attempting to minimize window for process with PID: {pid}")
        try:
            minimize_window_by_pid(pid)
        except Exception as e:
            if logger:
                logger.error(f"Failed to minimize window for process with PID {pid}: {e}")
            pass

def upgrade(to_version, executable_sha256, executable_zip_urls, stop_event=None):
    if not pyappify_upgradeable or (to_version and to_version.lstrip('v') == pyappify_version.lstrip('v')):
        return

    def _do_upgrade():
        tmp_dir = os.path.join(os.getcwd(), "tmp")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            downloaded_zip_path = None
            for url in executable_zip_urls:
                try:
                    local_zip_path = os.path.join(tmp_dir, os.path.basename(url))
                    with urllib.request.urlopen(url) as response, open(local_zip_path, 'wb') as out_file:
                        while True:
                            if stop_event and stop_event.is_set():
                                if logger:
                                    logger.info("Upgrade download cancelled by stop event.")
                                return
                            chunk = response.read(8192)
                            if not chunk:
                                break
                            out_file.write(chunk)
                    downloaded_zip_path = local_zip_path
                    break
                except Exception as e:
                    if logger:
                        logger.warning(f"Failed to download from {url}: {e}")
                    continue

            if not downloaded_zip_path:
                if logger:
                    logger.error("Failed to download upgrade.")
                return

            with zipfile.ZipFile(downloaded_zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmp_dir)

            new_executable_name = os.path.basename(pyappify_executable)
            found_executable_path = None
            for root, _, files in os.walk(tmp_dir):
                if new_executable_name in files:
                    found_executable_path = os.path.join(root, new_executable_name)
                    break

            if not found_executable_path:
                if logger:
                    logger.error("Executable not found in zip.")
                return

            sha256_hash = hashlib.sha256()
            with open(found_executable_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)

            if sha256_hash.hexdigest() != executable_sha256:
                if logger:
                    logger.error("SHA256 checksum mismatch.")
                return

            kill_pyappify()
            shutil.move(found_executable_path, pyappify_executable)
        except Exception as e:
            if logger:
                logger.error(f"Upgrade failed: {e}")
        finally:
            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir)

    thread = threading.Thread(target=_do_upgrade)
    thread.daemon = True
    thread.start()