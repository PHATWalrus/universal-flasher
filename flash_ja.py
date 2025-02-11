import os
import sys
import platform
import subprocess
import requests
import zipfile
import tarfile
import stat
import re
import shutil
import ctypes
import colorama
import json
import time
import threading
from datetime import datetime
from packaging import version
from typing import Optional, List, Dict
# Initialize colorama for Windows console colors
colorama.init()

class Flash:
    def __init__(self):
        self.system = platform.system().lower()
        self.arch = platform.machine().lower()
        self.fastboot_path = None
        self.adb_path = None
        self.platform_tools_url = self.get_platform_tools_url()
        self.work_dir = os.getcwd()
        self.min_version = version.parse("34.0.0")
        self.log_file = f"flash_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        self.install_path = r"C:\adb" if sys.platform == "win32" else None
        self.slot = "a"
        self.spinner_running = False
        self.current_device = None
        # Color setup using colorama
        self.color_red = colorama.Fore.RED
        self.color_green = colorama.Fore.GREEN
        self.color_yellow = colorama.Fore.YELLOW
        self.color_reset = colorama.Style.RESET_ALL

        # Load device configurations
        self.devices = self.load_device_config()
        if not self.devices:
            print(f"{self.color_red}サポートされているデバイスが見つかりません。終了します。{self.color_reset}")
            sys.exit(1)
        self.current_device = None
        self.boot_partitions = []
        self.vbmeta_partitions = []
        self.slot_specific_partitions = []
        self.logical_partitions = []
        self.firmware_partitions = []
        self.disable_avb = False
    def set_current_device(self, device):
        self.current_device = device
        self.boot_partitions = device['partitions'].get('boot', [])
        self.firmware_partitions = device['partitions'].get('firmware', [])
        self.logical_partitions = device['partitions'].get('logical', [])
        self.vbmeta_partitions = device['partitions'].get('vbmeta', [])
        self.slot_specific_partitions = device.get('slot_specific', [])
        
        # New board verification
        self.verify_board_compatibility()

    def verify_board_compatibility(self):
        if "board" not in self.current_device:
            return

        try:
            result = self.run_command([self.fastboot_path, "getvar", "product"])
            product_line = [line for line in result.stdout.splitlines() if line.startswith("product:")][0]
            current_board = product_line.split(":")[1].strip()
        except Exception as e:
            print(f"{self.color_red}デバイスボードの検証に失敗しました: {str(e)}{self.color_reset}")
            return

        expected_board = self.current_device["board"]
        if current_board.lower() != expected_board.lower():
            print(f"\n{self.color_red}警告: ボードの不一致が検出されました!{self.color_reset}")
            print(f"指定のボード: {expected_board}")
            print(f"現在のボード: {current_board}")
            if not self.prompt_yes_no("ボードが不一致ですが Flash を続行しますか?", default_yes=False):
                print(f"{self.color_red}Flash の手順を中止しています{self.color_reset}")
                sys.exit(1)

    def load_device_config(self) -> List[Dict]:
        try:
            url = "https://raw.githubusercontent.com/PHATWalrus/universal-flasher/refs/heads/main/devices.json"
            response = requests.get(url)
            response.raise_for_status()
            return json.loads(response.text)["devices"]
        except Exception as e:
            print(f"{self.color_red}デバイス設定の読み込みに失敗しました: {str(e)}{self.color_reset}")
            print(f"{self.color_yellow}インターネット接続またはリポジトリの URL を確認してください{self.color_reset}")
            sys.exit(1)

    def select_device(self):
        if not self.devices:
            print(f"{self.color_red}サポートしているデバイスが見つかりません。終了します。{self.color_reset}")
            sys.exit(1)
        
        print(f"\n{self.color_green}## サポートしているデバイス ##{self.color_reset}")
        for i, device in enumerate(self.devices, 1):
            print(f"{self.color_yellow}{i}. {device['model']}{self.color_reset}")
        
        while True:
            try:
                choice = int(input(f"\n{self.color_green}デバイスを選択してください (1-{len(self.devices)}): {self.color_reset}"))
                if 1 <= choice <= len(self.devices):
                    self.current_device = self.devices[choice - 1]
                    print(f"{self.color_green}選択したデバイス: {self.current_device['model']}{self.color_reset}")
                    self.set_current_device(self.current_device)
                    break
                else:
                    print(f"{self.color_red}選択が無効です。再度お試しください。{self.color_reset}")
            except ValueError:
                print(f"{self.color_red}入力が無効です。数字を入力してください。{self.color_reset}")


    def get_platform_tools_url(self):
        base_url = "https://dl.google.com/android/repository/platform-tools-latest-"
        if self.system == "windows":
            return f"{base_url}windows.zip"
        elif self.system == "linux":
            if "aarch64" in self.arch or "arm64" in self.arch:
                return f"{base_url}linux-arm64.tar.gz"
            return f"{base_url}linux.tar.gz"
        elif self.system == "darwin":
            return f"{base_url}darwin.zip"
        raise Exception("Unsupported operating system")

    def setup_environment(self):
        print(f"\n{self.color_green}## 環境の設定 ##{self.color_reset}")
        if self.system == "windows":
            self.handle_windows_installation()
        self.check_system_tools()
        if not self.fastboot_path:
            self.setup_bundled_tools()
        self.validate_tools()

    def start_spinner(self):
        if not self.spinner_running:
            self.spinner_running = True
            spinner_chars = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]
            def spin():
                i = 0
                while self.spinner_running:
                    sys.stdout.write(f"\r{self.color_yellow}{spinner_chars[i % len(spinner_chars)]} 実行中...{self.color_reset}")
                    sys.stdout.flush()
                    time.sleep(0.1)
                    i += 1
                sys.stdout.write("\r" + " " * 50 + "\r")
            self.spinner_thread = threading.Thread(target=spin, daemon=True)
            self.spinner_thread.start()

    def stop_spinner(self):
        if self.spinner_running:
            self.spinner_running = False
            if self.spinner_thread.is_alive():
                self.spinner_thread.join(timeout=0.3)
            time.sleep(0.1)  # Allow final spinner update to clear

    def handle_windows_installation(self):
        try:
            if not self.is_admin() and not self.check_system_tools():
                print(f"{self.color_yellow}システムの変更には管理者権限が必要です{self.color_reset}")
                if self.prompt_yes_no("管理者権限で再起動しますか?", default_yes=True):
                    self.run_as_admin()
                    exit(1)
            
            tools_path = os.path.join(self.install_path, "platform-tools")
            if not os.path.exists(tools_path):
                print(f"{self.color_yellow}Platform Tools のインストール - {self.install_path}{self.color_reset}")
                self.setup_bundled_tools()
            if self.is_admin() and self.check_system_tools():
                self.add_to_system_path(tools_path)
                print(f"{self.color_green}パスを追加しました: {tools_path}{self.color_reset}")
            elif not self.is_admin() and not self.check_system_tools():
                print(f"{self.color_yellow}システムパスを更新するには管理者権限が必要です{self.color_reset}")
                print(f"{self.color_yellow}管理者権限でスクリプトを実行してください{self.color_reset}")
                sys.exit(1)
            else:
                print(f"{self.color_yellow}パスの更新をスキップします{self.color_reset}")

        except Exception as e:
            print(f"{self.color_red}インストールに失敗しました: {str(e)}{self.color_reset}")
            sys.exit(1)

    def setup_bundled_tools(self):
        print(f"{self.color_yellow}バンドルされた Platform Tools を設定中です...{self.color_reset}")
        if self.system == "windows":
            tools_dir = self.install_path
        else:
            tools_dir = os.path.join(self.work_dir, "platform-tools")
        
        if not os.path.exists(tools_dir):
            self.download_and_extract_tools(tools_dir)
        
        self.fastboot_path = os.path.join(tools_dir, "platform-tools", "fastboot")
        if self.system == "windows":
            self.fastboot_path += ".exe"

    def download_and_extract_tools(self, extract_dir):
        os.makedirs(extract_dir, exist_ok=True)
        download_path = os.path.join(extract_dir, 
                                   "platform-tools.zip" if self.system == "windows" 
                                   else "platform-tools.tar.gz")
        
        try:
            with requests.get(self.platform_tools_url, stream=True) as r:
                r.raise_for_status()
                with open(download_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            if self.system == "windows":
                with zipfile.ZipFile(download_path) as zip_ref:
                    zip_ref.extractall(extract_dir)
            else:
                with tarfile.open(download_path, "r:gz") as tar_ref:
                    tar_ref.extractall(extract_dir)
            
            if self.system != "windows":
                bin_path = os.path.join(extract_dir, "platform-tools", "fastboot")
                st = os.stat(bin_path)
                os.chmod(bin_path, st.st_mode | stat.S_IEXEC)
        except Exception as e:
            print(f"{self.color_red}展開に失敗しました: {str(e)}{self.color_reset}")
            sys.exit(1)
        finally:
            if os.path.exists(download_path):
                os.remove(download_path)

    def add_to_system_path(self, path):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                               r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
                               0, winreg.KEY_ALL_ACCESS)
                               
            current_path = winreg.QueryValueEx(key, 'Path')[0]
            if path not in current_path:
                new_path = f"{current_path};{path}"
                winreg.SetValueEx(key, 'Path', 0, winreg.REG_EXPAND_SZ, new_path)
                winreg.CloseKey(key)
                ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, 'Environment', 0, 5000)
                print(f"{self.color_green}システムパスを更新しました{self.color_reset}")
                
                # New reboot sequence
                if self.system == "windows":
                    print(f"{self.color_yellow}変更を適用するためにシステムは 5 秒以内に再起動します...{self.color_reset}")
                    time.sleep(5)
                    subprocess.run(["shutdown", "/r", "/t", "0"], shell=True)
                
        except Exception as e:
            print(f"{self.color_red}パスの更新に失敗しました: {str(e)}{self.color_reset}")

    def validate_tools(self):
        try:
            result = self.run_command([self.fastboot_path, "--version"])
            if result.returncode != 0:
                raise Exception("Fastboot の検証に失敗しました")
            print(f"{self.color_green}Fastboot を検証しました: {result.stdout.strip()}{self.color_reset}")
        except Exception as e:
            print(f"{self.color_red}Fastboot エラー: {str(e)}{self.color_reset}")
            sys.exit(1)
    def check_system_tools(self):
        """既存のシステムツールを確認してバージョンを検証"""
        #print(f"{self.color_green}システムツールを確認中です...{self.color_reset}")
        
        # Check for fastboot
        self.fastboot_path = self.check_tool_version("fastboot")
        if not self.fastboot_path:
            print(f"{self.color_yellow}システム Fastboot が見つからないか、古くなっています{self.color_reset}")
            return False
        
        # Check for adb
        self.adb_path = self.check_tool_version("adb")
        if not self.adb_path:
            print(f"{self.color_yellow}システム ADB が見つからないか、古くなっています{self.color_reset}")
            return False
        return True

    def check_tool_version(self, tool):
        """システムツールが存在し、バージョン要件を満たしているかどうかを確認"""
        path = shutil.which(tool)
        if not path:
            return None
        
        try:
            if tool=="fastboot":
                result = subprocess.run([tool, "--version"], 
                                    capture_output=True, 
                                    text=True)
                version_match = re.search(r"(\d+\.\d+\.\d+)", result.stdout)
            elif tool=="adb":
                result = subprocess.run([tool, "version"], 
                                    capture_output=True, 
                                    text=True)
                version_match = re.search(r"(\d+\.\d+\.\d+)-", result.stdout)

            if version_match and version.parse(version_match.group(1)) >= self.min_version:
                #print(f"{self.color_green}使用中のシステム: {tool} v{version_match.group(1)}{self.color_reset}")
                return path
        except Exception as e:
            print(f"{self.color_yellow}{tool} のバージョンの確認に失敗しました: {str(e)}{self.color_reset}")
        return None
    
    def is_admin(self):
        if self.system != "windows":
            return False
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False

    def run_as_admin(self):
        script = os.path.abspath(sys.argv[0])
        params = ' '.join(['--nopause'] + sys.argv[1:])
        try:
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{script}" {params}', None, 1)
            sys.exit(0)
        except:
            print(f"{self.color_red}管理者権限で再起動できませんでした{self.color_reset}")
            return False


    def ask_slot_selection(self):
        print(f"\n{self.color_green}## スロットの選択 ##{self.color_reset}")
        if self.prompt_yes_no("両方のスロット (A/B) に Flash しますか?", default_yes=True):
            self.slot = "both"
            print(f"{self.color_yellow}選択済み: 両方のスロットに Flash{self.color_reset}")
        else:
            self.slot = "current"
            print(f"{self.color_yellow}選択済み: 現在のスロットのみに Flash{self.color_reset}")

    def check_prerequisites(self):
        checks = [
            ("Bootloader はアンロックされていますか?", True),
            ("デバイスは Fastboot モードになっていますか?", True),
            ("USB ドライバーは適切に設定されていますか?", True)
        ]
        
        for question, required in checks:
            if not self.prompt_yes_no(question):
                if required:
                    print(f"{self.color_red}必須要件が満たされていません{self.color_reset}")
                    print(f"{self.color_yellow}こちらをご確認ください: https://developer.android.com/tools/device")
                    sys.exit(1)
                print(f"{self.color_yellow}警告: 問題が発生する可能性があります{self.color_reset}")

    def device_checks(self):
        try:
            result = self.run_command([self.fastboot_path, "devices"])
            devices = [line.split()[0] for line in result.stdout.splitlines() 
                      if "fastboot" in line.lower()]
            
            if not devices:
                print(f"{self.color_red}デバイスが検出されませんでした{self.color_reset}")
                print(f"{self.color_yellow}1. デバイスが Fastboot モードになっているか確認してください")
                print("2. USB の接続を確認してください")
                if self.system == "linux":
                    print("3. udev ルールを確認してください")
                sys.exit(1)
            
            print(f"{self.color_green}接続されているデバイス: {devices[0]}{self.color_reset}")
            self.select_device()
            
        except Exception as e:
            print(f"{self.color_red}デバイスの確認に失敗しました: {str(e)}{self.color_reset}")
            sys.exit(1)
    
    def handle_super_partitions(self):
        print(f"\n{self.color_green}## SUPER パーティションの処理 ##{self.color_reset}")
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        os.chdir(exe_dir)
        
        super_files = []
        if os.path.exists('super_empty.img'):
            super_files.append('super_empty')
        if os.path.exists('super.img'):
            super_files.append('super')
        
        # Prioritize super_empty first if both exist
        super_files = sorted(super_files, key=lambda x: x == 'super')
        
        if not super_files:
            print(f"{self.color_yellow}Super パーティションが見つかりません{self.color_reset}")
            return False
        
        try:
            self.start_spinner()
            self.resize_partitions()
            for part in super_files:
                print(f"{self.color_green}{part} を Flash 中です...{self.color_reset}")
                self.flash_partitions([part])
            self.stop_spinner()
            return True
        except Exception as e:
            self.stop_spinner()
            print(f"{self.color_red}Super パーティションの Flash エラー: {str(e)}{self.color_reset}")
            sys.exit(1)

    def flash_procedure(self):
        if self.spinner_running:
            self.spinner_running = False
            if self.spinner_thread.is_alive():
                self.spinner_thread.join(timeout=0.3)
            time.sleep(0.1)
        try:
            self.ask_slot_selection()
            self.run_command([self.fastboot_path, "--set-active=a"])
            
            if self.prompt_yes_no("userdata を消去しますか? (クリーンインストールで推奨)"):
                if self.confirm_operation("すべての USERDATA を消去", dangerous=True):
                    self.start_spinner()
                    self.run_command([self.fastboot_path, "-w"])
                    self.stop_spinner()

            self.handle_boot_partitions()
            self.handle_vbmeta()
            self.handle_fastbootd_reboot()
            sup_stat=self.handle_super_partitions()
            #print (f"sup_stat={sup_stat}")
            if sup_stat==False:
                self.handle_logical_partitions()
            self.handle_firmware()

            if self.prompt_yes_no("システムを再起動しますか?"):
                self.start_spinner()
                self.run_command([self.fastboot_path, "reboot"])
                self.stop_spinner()

            print(f"{self.color_green}\nFlashing が正常に完了しました{self.color_reset}")
            self.generate_summary_report()

        except subprocess.CalledProcessError as e:
            self.stop_spinner()
            print(f"{self.color_red}Flashing エラー: {str(e)}{self.color_reset}")
            sys.exit(1)

    def handle_boot_partitions(self):
        if self.spinner_running:
            self.spinner_running = False
            if self.spinner_thread.is_alive():
                self.spinner_thread.join(timeout=0.3)
            time.sleep(0.1)
        boot_files = self.filter_existing(self.boot_partitions)
        if not self.confirm_flash(boot_files, "boot"):
            if self.prompt_yes_no("Flash のプロセス全体を中止しますか?"):
                sys.exit(1)
            return
        self.start_spinner()
        self.flash_partitions(boot_files)
        self.stop_spinner()

    def handle_vbmeta(self):
        if self.spinner_running:
            self.spinner_running = False
            if self.spinner_thread.is_alive():
                self.spinner_thread.join(timeout=0.3)
            time.sleep(0.1)
        self.disable_avb = self.prompt_yes_no("確認付きブート (AVB) を無効化しますか?")
        avb_flags = ["--disable-verity", "--disable-verification"] if self.disable_avb else []
        
        self.start_spinner()
        for part in self.vbmeta_partitions:
            img_file = f"{part}.img"
            if part == "preloader_raw":
                self.run_command([self.fastboot_path, "flash"] + ["preloader", img_file])
            if os.path.exists(img_file):
                self.run_command([self.fastboot_path, "flash"] + avb_flags + [part, img_file])
        self.stop_spinner()

    def handle_fastbootd_reboot(self):
        if self.spinner_running:
            self.spinner_running = False
            if self.spinner_thread.is_alive():
                self.spinner_thread.join(timeout=0.3)
            time.sleep(0.1)
        print(f"\n{self.color_green}## FASTBOOTD で再起動中 ##{self.color_reset}")
        self.start_spinner()
        self.run_command([self.fastboot_path, "reboot", "fastboot"])
        self.stop_spinner()

    def handle_logical_partitions(self):
        if self.spinner_running:
            self.spinner_running = False
            if self.spinner_thread.is_alive():
                self.spinner_thread.join(timeout=0.3)
            time.sleep(0.1)
        logical_files = self.filter_existing(self.logical_partitions)
        missing = self.get_missing_partitions(self.logical_partitions)
        
        self.display_missing_report(missing, "logical")
        if not self.confirm_flash(logical_files, "logical"):
            sys.exit(1)
        
        self.start_spinner()
        self.resize_partitions()
        self.flash_partitions(logical_files)
        self.stop_spinner()

    def handle_firmware(self):
        if self.spinner_running:
            self.spinner_running = False
            if self.spinner_thread.is_alive():
                self.spinner_thread.join(timeout=0.3)
            time.sleep(0.1)
        firmware_files = self.filter_existing(self.firmware_partitions)
        missing = self.get_missing_partitions(self.firmware_partitions)
        
        self.display_missing_report(missing, "firmware")
        if not self.confirm_flash(firmware_files, "firmware"):
            sys.exit(1)
        self.start_spinner()
        self.flash_partitions(firmware_files)
        self.stop_spinner()

    def filter_existing(self, partitions):
        return [p for p in partitions if os.path.exists(f"{p}.img")]

    def get_missing_partitions(self, partitions):
        return [p for p in partitions if not os.path.exists(f"{p}.img")]

    def display_missing_report(self, missing, category):
        if missing:
            print(f"\n{self.color_yellow}[{category.upper()} ファイルが見つかりません]{self.color_reset}")
            for part in missing:
                print(f" - {part}.img")
            print(f"{self.color_yellow}すべての不足している物: {len(missing)}/{len(getattr(self, f'{category}_partitions'))}{self.color_reset}")

    def confirm_flash(self, partitions, name):
        expected_count = len(getattr(self, f'{name}_partitions'))
        found_count = len(partitions)
        
        print(f"\nFound {found_count}/{expected_count} {name} files")
        if found_count < expected_count:
            missing = list(set(getattr(self, f'{name}_partitions')) - set(partitions))
            print(f"見つからないファイルは {len(missing)} 個です:")
            for part in missing:
                print(f" - {part}.img")
        
        if found_count == 0:
            print(f"ディレクトリ内に {name} ファイルが見つかりません")
            return self.prompt_yes_no(f"{name} パーティションを Flash しないで続行しますか?")
            
        return self.prompt_yes_no(f"Flash 可能な {name} パーティションはありますか?")

    def confirm_operation(self, operation, dangerous=False):
        color = self.color_red if dangerous else self.color_yellow
        print(f"\n{color}確認: {operation}?{self.color_reset}")
        return self.prompt_yes_no("確認するには 'yes' と入力", confirmation=True)

    def resize_partitions(self):
        print(f"{self.color_yellow}論理パーティションをリサイズ中です...{self.color_reset}")
        for part in self.logical_partitions:
            for slot in ["a", "b"]:
                self.run_command([self.fastboot_path, "delete-logical-partition", f"{part}_{slot}-cow"])
                self.run_command([self.fastboot_path, "delete-logical-partition", f"{part}_{slot}"])
                self.run_command([self.fastboot_path, "create-logical-partition", f"{part}_{slot}", "1"])

    def flash_partitions(self, partitions):
        if not partitions:
            print("このカテゴリーに Flash するファイルはありません")
            return
            
        for part in partitions:
            img_file = f"{part}.img"
            if not os.path.exists(img_file):
                print(f"{self.color_yellow}{part} をスキップ - ファイルがありません{self.color_reset}")
                continue
                
            if self.slot == "both" and part in self.slot_specific_partitions:
                for slot in ['a', 'b']:
                    slot_part = f"{part}_{slot}"
                    print(f"{self.color_green}{slot_part} を Flash 中です...{self.color_reset}")
                    self.run_command([self.fastboot_path, "flash", slot_part, img_file])
            else:
                print(f"{self.color_green}{part} を Flash 中です...{self.color_reset}")
                self.run_command([self.fastboot_path, "flash", part, img_file])

    def run_command(self, cmd):
        log_entry = f"[{datetime.now().isoformat()}] COMMAND: {' '.join(cmd)}\n"
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True
            )
            log_entry += f"OUTPUT:\n{result.stdout.strip()}\n"
            #print(f"\n{self.color_green}[コマンド]{self.color_reset} {' '.join(cmd)}")
            print(f"{self.color_green}[出力]{self.color_reset}\n{result.stdout.strip()}")
            return result
        except subprocess.CalledProcessError as e:
            log_entry += f"エラー: {e.stdout}\n終了コード: {e.returncode}"
            print(f"\n{self.color_red}[コマンドが失敗]{self.color_reset} {' '.join(cmd)}")
            print(f"{self.color_red}[エラー]{self.color_reset}\n{e.stdout}")
            raise
        finally:
            self.write_to_log(log_entry)

    def write_to_log(self, content):
        with open(self.log_file, "a") as f:
            f.write(content + "\n")

    def generate_summary_report(self):
        summary = "\n=== FLASH のサマリー ===\n"
        summary += f"ログファイル: {self.log_file}\n"
        summary += f"タイムスタンプ: {datetime.now().isoformat()}\n"
        self.write_to_log(summary)

    def prompt_yes_no(self, question, confirmation=False, default_yes=False):
        prompt_suffix = " [Y/n]" if default_yes else " [y/N]"
        while True:
            response = input(f"{self.color_yellow}{question}{prompt_suffix}: {self.color_reset}").lower()
            if response == "" and default_yes:
                return True
            if response in ["y", "yes"]:
                return True
            if response in ["n", "no"]:
                return False
            print(f"{self.color_red}入力が無効です。 y/n を入力してください。{self.color_reset}")

    def display_main_menu(self):
        ascii_art = """
        ███████╗██╗      █████╗ ███████╗██╗  ██╗███████╗██████╗ 
        ██╔════╝██║     ██╔══██╗██╔════╝██║  ██║██╔════╝██╔══██╗
        █████╗  ██║     ███████║███████╗███████║█████╗  ██████╔╝
        ██╔══╝  ██║     ██╔══██║╚════██║██╔══██║██╔══╝  ██╔══██╗
        ██║     ███████╗██║  ██║███████║██║  ██║███████╗██║  ██║
        ╚═╝     ╚══════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
        """
        print(f"{self.color_green}{ascii_art}{self.color_reset}")
        print(f"{self.color_yellow}Android ROM Flasher へようこそ{self.color_reset}")
        print(f"\n{self.color_green}1. ROM を Flash{self.color_reset}")
        print(f"{self.color_green}2. 終了{self.color_reset}")

        while True:
            choice = input(f"\n{self.color_yellow}選択を入力してください (1-2): {self.color_reset}")
            if choice == '1':
                self.check_prerequisites()
                self.device_checks()
                self.flash_procedure()
                break
            elif choice == '2':
                print(f"{self.color_yellow}終了中...{self.color_reset}")
                sys.exit(0)
            else:
                print(f"{self.color_red}入力が無効です。再度お試しください。{self.color_reset}")
def main():
    try:
        flasher = Flash()
        flasher.setup_environment()
        flasher.display_main_menu()
    except SystemExit as e:
        print(f"\n{colorama.Fore.YELLOW}プロセスは {e.code} で終了しました{colorama.Style.RESET_ALL}")
    except KeyboardInterrupt:
        print(f"\n{colorama.Fore.RED}操作はユーザーによってキャンセルされました{colorama.Style.RESET_ALL}")
    except Exception as e:
        print(f"\n{colorama.Fore.RED}予期しないエラー: {str(e)}{colorama.Style.RESET_ALL}")
    finally:
        input("ウィンドウを閉じるには Enter を押してください...")


if __name__ == "__main__":
    if "--nopause" not in sys.argv:
        main()
    else:
        sys.argv.remove("--nopause")
        main()
