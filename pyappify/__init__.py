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
    if not pyappify_upgradeable or not is_greater_version(to_version, pyappify_version):
        if logger:
            logger.info(f"pyappify no need to upgrade {pyappify_upgradeable} {to_version} {executable_sha256} {executable_zip_urls}")
        return
    if logger:
        logger.info(
            f"pyappify start to upgrade {pyappify_upgradeable} {to_version} {executable_sha256} {executable_zip_urls}")
    def _do_upgrade():
        tmp_dir = os.path.join(os.getcwd(), "pyappify_tmp")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            downloaded_zip_path = None
            for url in executable_zip_urls:
                try:
                    if logger:
                        logger.info(
                            f"pyappify start to download {url}")
                    local_zip_path = os.path.join(tmp_dir, os.path.basename(url))
                    with urllib.request.urlopen(url) as response, open(local_zip_path, 'wb') as out_file:
                        while True:
                            if stop_event and stop_event.is_set():
                                if logger:
                                    logger.info("pyappify Upgrade download cancelled by stop event.")
                                return
                            chunk = response.read(8192)
                            if not chunk:
                                break
                            out_file.write(chunk)
                    downloaded_zip_path = local_zip_path
                    if logger:
                        logger.info(
                            f"pyappify download success {url}")
                    break
                except Exception as e:
                    if logger:
                        logger.warning(f"pyappify Failed to download from {url}: {e}")
                    continue

            if not downloaded_zip_path:
                if logger:
                    logger.error("pyappify Failed to download upgrade.")
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
                    logger.error("pyappify Executable not found in zip.")
                return

            sha256_hash = hashlib.sha256()
            with open(found_executable_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)

            if sha256_hash.hexdigest() != executable_sha256:
                if logger:
                    logger.error("pyappify SHA256 checksum mismatch.")
                return

            kill_pyappify()
            shutil.move(found_executable_path, pyappify_executable)
            if logger:
                logger.info(f"pyappify Upgrade success")
        except Exception as e:
            if logger:
                logger.error(f"pyappify Upgrade failed: {e}")
        finally:
            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir)

    thread = threading.Thread(target=_do_upgrade)
    thread.daemon = True
    thread.start()

def is_greater_version(version1, version2):
    """
    Compares two semantic version strings.

    Args:
        version1 (str): The first version string.
        version2 (str): The second version string.

    Returns:
        bool: True if version1 is strictly greater than version2,
              False otherwise or if parsing fails.
    """
    try:
        version1 = version1.lstrip('v')
        version2 = version2.lstrip('v')
        v1_parts = [int(p) for p in version1.split('.')]
        v2_parts = [int(p) for p in version2.split('.')]
        return v1_parts > v2_parts
    except (ValueError, AttributeError):
        return False