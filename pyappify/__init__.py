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

try:
    pid = int(os.environ.get("PYAPPIFY_PID"))
except (ValueError, TypeError):
    pid = None


def kill_pyappify():
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def upgrade(to_version, executable_sha256, executable_zip_urls, logger=None, stop_event=None):
    if not pyappify_upgradeable or to_version == pyappify_version:
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