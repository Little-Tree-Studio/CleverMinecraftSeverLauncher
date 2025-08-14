import flet as ft
from flet_audio.audio import Audio
import time
import subprocess
import threading
import psutil
import json
import uuid
import os
import datetime
import zipfile
import requests
import re
import shutil
from typing import Optional, List, Any
try:
    import winreg
except ImportError:
    winreg = None # For non-Windows platforms, though this script is Windows-centric
try:
    import yaml
except ImportError:
    yaml = None

SETTINGS_FILE = "settings.json"
app_settings = {}

def load_settings():
    global app_settings
    defaults = {
        "theme": "system",
        "primary_color": ft.Colors.BLUE_GREY,
        "java_path": "",
        "jvm_args": "-Xmx1024M -Xms1024M",
        "download_source": "Official"
    }
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                app_settings = json.load(f)
            for key, value in defaults.items():
                if key not in app_settings:
                    app_settings[key] = value
        else:
            app_settings = defaults
            save_settings()
    except Exception as e:
        print(f"Error loading settings: {e}")
        app_settings = defaults

def save_settings():
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(app_settings, f, indent=4)
    except Exception as e:
        print(f"Error saving settings: {e}")

def str_to_theme_mode(s: str) -> ft.ThemeMode:
    if s == "dark":
        return ft.ThemeMode.DARK
    if s == "light":
        return ft.ThemeMode.LIGHT
    return ft.ThemeMode.SYSTEM

def find_all_java_executables():
    """
    Scans for all Java executables on a Windows system using multiple strategies.
    """
    found_paths = set()

    # --- Strategy 1: Windows Registry (Most reliable) ---
    if winreg:
        def search_registry_key(key, subkey_path):
            try:
                with winreg.OpenKey(key, subkey_path) as subkey:
                    for i in range(winreg.QueryInfoKey(subkey)[0]):
                        try:
                            version_name = winreg.EnumKey(subkey, i)
                            with winreg.OpenKey(subkey, version_name) as version_key:
                                java_home, _ = winreg.QueryValueEx(version_key, 'JavaHome')
                                java_exe = os.path.join(java_home, 'bin', 'java.exe')
                                if os.path.isfile(java_exe):
                                    found_paths.add(os.path.normpath(java_exe))
                        except FileNotFoundError:
                            continue
            except FileNotFoundError:
                pass  # Key doesn't exist, which is fine

        registry_paths_to_check = [
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\JavaSoft\Java Runtime Environment'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\JavaSoft\Java Development Kit'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Eclipse Foundation\JDKs'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Amazon\Corretto'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\BellSoft\Liberica'),
            (winreg.HKEY_CURRENT_USER, r'SOFTWARE\JavaSoft\Java Runtime Environment'),
            (winreg.HKEY_CURRENT_USER, r'SOFTWARE\JavaSoft\Java Development Kit'),
        ]
        for key, path in registry_paths_to_check:
            search_registry_key(key, path)

    # --- Strategy 2: JAVA_HOME environment variable ---
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        path = os.path.join(java_home, "bin", "java.exe")
        if os.path.isfile(path):
            found_paths.add(os.path.normpath(path))

    # --- Strategy 3: PATH environment variable ---
    path_dirs = os.environ.get('PATH', '').split(os.pathsep)
    for path_dir in path_dirs:
        java_path = os.path.join(path_dir, 'java.exe')
        if os.path.isfile(java_path):
            try:
                # Resolve symlinks and get the real path to avoid duplicates
                real_path = os.path.realpath(java_path)
                found_paths.add(os.path.normpath(real_path))
            except Exception:
                found_paths.add(os.path.normpath(java_path))

    # --- Strategy 4: Manual Scan of common directories (Fallback) ---
    search_dirs = [os.environ.get("ProgramFiles", "C:\\Program Files"), os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")]
    for search_dir in search_dirs:
        if not os.path.isdir(search_dir): continue
        
        for subdir_name in os.listdir(search_dir):
            if any(keyword in subdir_name.lower() for keyword in ["java", "jdk", "jre", "adopt", "corretto", "bellsoft", "microsoft", "oracle"]):
                potential_path = os.path.join(search_dir, subdir_name)
                if os.path.isdir(potential_path):
                    for root, _, files in os.walk(potential_path):
                        if "java.exe" in files and "bin" in root.lower():
                            found_paths.add(os.path.normpath(os.path.join(root, "java.exe")))

    return sorted(list(found_paths), reverse=True)

def find_java_executable():
    paths = find_all_java_executables()
    return paths[0] if paths else None

REQUESTS_HEADERS = {
    'User-Agent': 'SCL/1.0.0'
}

def get_api_base_url(service: str) -> str:
    """Returns the base URL for a given service based on the download source setting."""
    
    official_sources = {
        "mojang_meta": "https://launchermeta.mojang.com",
        "mojang_api": "https://api.mojang.com",
        "paper": "https://fill.papermc.io", # Updated to the new v3 API base URL
        "purpur": "https://api.purpurmc.org",
        "modrinth": "https://api.modrinth.com",
        "hangar": "https://hangar.papermc.io/api/v1", # PaperMC Plugins (Hangar)
        "getbukkit_page": "https://getbukkit.org",
        "getbukkit_cdn": "https://cdn.getbukkit.org"
    }

    source = app_settings.get("download_source", "Official")
    if source == "CMCLAPI (China Mirror)":
        mirror_sources = {
            "mojang_meta": "https://bmclapi2.bangbang93.com",
            "mojang_api": "https://bmclapi2.bangbang93.com",
            "paper": "https://bmclapi2.bangbang93.com/papermc",
            "purpur": "https://bmclapi2.bangbang93.com/purpurmc",
            "modrinth": "https://bmclapi2.bangbang93.com/modrinth",
            # Note: Spigot (getbukkit) is intentionally not mirrored as BMCLAPI doesn't host it.
        }
        # Return mirror URL if available, otherwise fall back to official
        return mirror_sources.get(service, official_sources.get(service, ""))
        
    return official_sources.get(service, "")

def get_player_uuid(username: str) -> Optional[dict[str, Any]]:
    """Fetches a player's UUID and corrected name from the Mojang API."""
    try:
        url = f"{get_api_base_url('mojang_api')}/users/profiles/minecraft/{username}"
        response = requests.get(url, timeout=5, headers=REQUESTS_HEADERS)
        if response.status_code == 200:
            data = response.json()
            # Return name and UUID (id)
            return {"name": data.get("name"), "id": data.get("id")}
        return None
    except requests.RequestException:
        return None

def get_server_game_version(server_path: Optional[str]) -> Optional[str]:
    """Tries to extract the Minecraft game version from the server jar file name."""
    if not server_path or not os.path.isdir(server_path):
        return None
    try:
        jar_files = [f for f in os.listdir(server_path) if f.endswith('.jar')]
        if not jar_files:
            return None
        
        # Regex to find version numbers like 1.20.4 or 1.19. It's a best-effort guess.
        match = re.search(r'(\d{1,2}\.\d{1,2}(\.\d{1,2})?)', jar_files[0])
        if match:
            return match.group(1)
    except Exception:
        return None
    return None

def main(page: ft.Page):
    load_settings()
    if not app_settings.get("java_path"):
        found_java = find_java_executable()
        if found_java:
            app_settings["java_path"] = found_java
            save_settings()
    # --- Page and Window Styling (Fluent UI) ---
    page.title = "Minecraft Server Panel"
    page.window.width = 1200
    page.window.height = 800
    page.window.min_width = 900
    page.window.min_height = 700
    page.theme_mode = str_to_theme_mode(app_settings.get("theme", "system"))
    
    page.fonts = {
        "Roboto": "https://fonts.google.com/specimen/Roboto",
        "Roboto Mono": "https://fonts.google.com/specimen/Roboto+Mono"
    }

    primary_color = app_settings.get("primary_color", ft.Colors.BLUE_GREY)
    page.theme = ft.Theme(color_scheme_seed=primary_color, font_family="Roboto")
    page.dark_theme = ft.Theme(color_scheme_seed=primary_color, font_family="Roboto")

    completion_sound = Audio(src="https://www.soundjay.com/buttons/sounds/button-3.mp3", autoplay=False)
    page.overlay.append(completion_sound)

    # --- Global State and Constants ---
    SERVERS_ROOT_DIR = "servers"
    if not os.path.exists(SERVERS_ROOT_DIR):
        os.makedirs(SERVERS_ROOT_DIR)

    server_process = None
    server_thread = None
    performance_thread = None
    player_list_thread = None
    online_players = ft.Ref[list[str]]()
    online_players.current = []
    selected_server_path = ft.Ref[Optional[str]]()
    
    REQUESTS_TIMEOUT = 15

    # --- UI Helper Class for Themed Cards ---
    class SettingsCard(ft.Container):
        def __init__(self, title, controls, **kwargs):
            super().__init__(**kwargs)
            self.padding=15
            self.border_radius=ft.border_radius.all(10)
            self.border=ft.border.all(1, ft.Colors.with_opacity(0.1, ft.Colors.ON_SURFACE))
            self.bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.ON_SURFACE)
            self.margin=ft.margin.only(bottom=10)
            self.content = ft.Column([
                ft.Text(title, style=ft.TextThemeStyle.TITLE_MEDIUM, weight=ft.FontWeight.W_500),
                ft.Divider(height=5, color=ft.Colors.TRANSPARENT),
                ft.Column(controls, spacing=15)
            ])

    # --- All Logic Functions ---
    console_output = ft.ListView(expand=True, spacing=5, auto_scroll=True)
    command_input = ft.TextField(label="输入服务器命令...", expand=True, border_radius=ft.border_radius.all(8))

    server_status_text = ft.Text("服务器状态: 未运行", color=ft.Colors.RED, weight=ft.FontWeight.BOLD)
    player_count_text = ft.Text("玩家: 0/20")

    start_button = ft.FilledButton("启动服务器", icon=ft.Icons.PLAY_ARROW_ROUNDED, disabled=True)
    stop_button = ft.FilledButton("停止服务器", icon=ft.Icons.STOP_ROUNDED, disabled=True, style=ft.ButtonStyle(bgcolor=ft.Colors.RED_400))
    restart_button = ft.FilledButton("重启服务器", icon=ft.Icons.RESTART_ALT_ROUNDED, disabled=True)
    configure_button = ft.FilledButton("配置", icon=ft.Icons.EDIT_NOTE_ROUNDED, disabled=True, tooltip="编辑 server.properties")
    delete_server_button = ft.IconButton(
        icon=ft.Icons.DELETE_FOREVER_ROUNDED,
        tooltip="删除选定的服务器",
        disabled=True,
        icon_color=ft.Colors.RED_400
    )

    cpu_progress = ft.ProgressBar(width=400, value=0)
    cpu_text = ft.Text("CPU: 0%")
    ram_progress = ft.ProgressBar(width=400, value=0)
    ram_text = ft.Text("内存: 0 MB / 0 MB (0%)")

    def create_console_text(text: str, **kwargs):
        return ft.Text(text, font_family="Roboto Mono", **kwargs)

    def update_console_output():
        nonlocal server_process
        if not server_process or not server_process.stdout: return
        
        while server_process.poll() is None:
            try:
                line = server_process.stdout.readline()
                if not line: break
                
                cleaned_line = line.strip()

                # --- Player List Parsing ---
                # Vanilla: "There are 1 of 20 players online: Player123"
                # Paper: "[15:12:48 INFO]: There are 1 of 20 players online: Player123"
                list_match = re.search(r"players online: (.*)", cleaned_line)
                if list_match:
                    player_names_str = list_match.group(1).strip()
                    if player_names_str:
                        online_players.current = sorted([name.strip() for name in player_names_str.split(",")])
                    else:
                        online_players.current = []
                    page.update()
                    # Don't show this line in console, it's spammy
                    continue

                # Join: "Player123[/127.0.0.1:50168] logged in with entity id..." or "[INFO]: Player123 joined the game"
                join_match = re.search(r"(\w+)\[.*logged in|\]: (\w+) joined the game", cleaned_line)
                if join_match:
                    player_name = join_match.group(1) or join_match.group(2)
                    if player_name and player_name not in online_players.current:
                        online_players.current.append(player_name)
                        online_players.current.sort()
                        page.update()

                # Leave: "[INFO]: Player123 left the game"
                leave_match = re.search(r"\]: (\w+) left the game", cleaned_line)
                if leave_match:
                    player_name = leave_match.group(1)
                    if player_name and player_name in online_players.current:
                        online_players.current.remove(player_name)
                        page.update()

                console_output.controls.append(create_console_text(cleaned_line))
                page.update()
            except (IOError, ValueError):
                # This can happen if the process is terminated and the pipe closes unexpectedly.
                break
        
        server_process = None
        online_players.current = []
        server_status_text.value = "服务器状态: 未运行"
        server_status_text.color = ft.Colors.RED
        is_server_selected = selected_server_path.current is not None
        start_button.disabled = not is_server_selected
        stop_button.disabled = True
        restart_button.disabled = True
        configure_button.disabled = not is_server_selected
        delete_server_button.disabled = not is_server_selected
        console_output.controls.append(create_console_text("服务器已停止。", color=ft.Colors.RED))
        page.update()

    def update_player_list_periodically():
        nonlocal server_process
        while server_process and server_process.poll() is None:
            if server_process and server_process.stdin:
                try:
                    server_process.stdin.write("list\n")
                    server_process.stdin.flush()
                except (IOError, AttributeError):
                    break # Stop if pipe is broken
            time.sleep(10) # Send list command every 10 seconds

    def update_performance_stats():
        nonlocal server_process
        while server_process and server_process.poll() is None:
            try:
                p = psutil.Process(server_process.pid)
                cpu_percent = p.cpu_percent(interval=1)
                memory_info = p.memory_info()
                memory_usage_mb = memory_info.rss / (1024 * 1024)
                total_memory_mb = 1024
                memory_percent = (memory_usage_mb / total_memory_mb)
                cpu_progress.value = cpu_percent / 100
                cpu_text.value = f"CPU: {cpu_percent:.1f}%"
                ram_progress.value = memory_percent
                ram_text.value = f"内存: {memory_usage_mb:.0f} MB / {total_memory_mb} MB ({memory_percent*100:.1f}%)"
            except psutil.NoSuchProcess: break
            except Exception as e: print(f"Perf error: {e}")
            page.update()
            time.sleep(2)
        cpu_progress.value = 0
        cpu_text.value = "CPU: 0%"
        ram_progress.value = 0
        ram_text.value = "内存: 0 MB / 0 MB (0%)"
        page.update()

    def start_server(e):
        nonlocal server_process, server_thread, performance_thread, player_list_thread
        if not selected_server_path.current:
            console_output.controls.append(create_console_text("错误: 请先选择一个服务器实例。", color=ft.Colors.RED))
            page.update()
            return

        if server_process is None:
            server_dir = selected_server_path.current
            jar_files = [f for f in os.listdir(server_dir) if f.endswith('.jar')]
            if not jar_files:
                console_output.controls.append(create_console_text(f"错误: 在 '{os.path.basename(server_dir)}' 目录中未找到 .jar 文件。", color=ft.Colors.RED))
                page.update()
                return
            
            server_jar = jar_files[0]
            console_output.controls.clear()
            console_output.controls.append(create_console_text(f"正在启动服务器 '{os.path.basename(server_dir)}'...", color=ft.Colors.BLUE))
            page.update()
            try:
                # Explicitly check for a non-empty path to avoid falling back to "java" when an empty string is set
                java_executable = app_settings.get("java_path")
                if not java_executable:
                    java_executable = "java"
                
                jvm_args = app_settings.get("jvm_args", "-Xmx1024M -Xms1024M").split()
                
                command = [java_executable] + jvm_args + ["-jar", server_jar, "nogui"]
                console_output.controls.append(create_console_text(f"执行命令: {' '.join(command)}", color=ft.Colors.GREY))
                page.update()
                
                server_process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                    text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW, cwd=server_dir
                )
                server_thread = threading.Thread(target=update_console_output, daemon=True)
                server_thread.start()
                performance_thread = threading.Thread(target=update_performance_stats, daemon=True)
                performance_thread.start()
                player_list_thread = threading.Thread(target=update_player_list_periodically, daemon=True)
                player_list_thread.start()
                server_status_text.value = f"服务器状态: 运行中 ({os.path.basename(server_dir)})"
                server_status_text.color = ft.Colors.GREEN
                start_button.disabled = True
                stop_button.disabled = False
                restart_button.disabled = False
                configure_button.disabled = True
                delete_server_button.disabled = True
            except FileNotFoundError:
                console_output.controls.append(create_console_text("错误: 'java' 命令未找到。请确保已安装 Java 并将其添加至系统 PATH。", color=ft.Colors.RED))
            except Exception as ex:
                console_output.controls.append(create_console_text(f"启动失败: {ex}", color=ft.Colors.RED))
            page.update()

    def send_command(e):
        if server_process and server_process.stdin and command_input.value:
            try:
                command = command_input.value + "\n"
                server_process.stdin.write(command)
                server_process.stdin.flush()
                console_output.controls.append(create_console_text(f"> {command_input.value}", color=ft.Colors.CYAN))
                command_input.value = ""
            except Exception as ex:
                console_output.controls.append(create_console_text(f"命令发送失败: {ex}", color=ft.Colors.RED))
            page.update()

    def stop_server_action():
        if server_process and server_process.stdin:
            console_output.controls.append(create_console_text("正在停止服务器...", color=ft.Colors.ORANGE))
            page.update()
            try:
                server_process.stdin.write("stop\n")
                server_process.stdin.flush()
            except (IOError, AttributeError):
                if server_process:
                    server_process.terminate()

    def restart_server(e):
        console_output.controls.append(create_console_text("正在重启服务器...", color=ft.Colors.BLUE))
        page.update()
        stop_server_action()
        def wait_and_restart():
            if server_thread: server_thread.join()
            start_server(None)
        threading.Thread(target=wait_and_restart, daemon=True).start()

    start_button.on_click = start_server
    stop_button.on_click = lambda e: stop_server_action()
    restart_button.on_click = restart_server
    command_input.on_submit = send_command
    send_button = ft.IconButton(icon=ft.Icons.SEND_ROUNDED, on_click=send_command, tooltip="发送命令")

    # --- View Creation Functions ---
    def create_home_view():
        def copy_console_output(e):
            console_texts = [c.value for c in console_output.controls if isinstance(c, ft.Text) and c.value is not None]
            all_text = "\n".join(console_texts)
            page.set_clipboard(all_text)
            page.overlay.append(ft.SnackBar(ft.Text("控制台内容已复制到剪贴板。"), open=True))
            page.update()

        def open_properties_editor(e):
            if not selected_server_path.current:
                page.overlay.append(ft.SnackBar(ft.Text("请先选择一个服务器!"), open=True))
                page.update()
                return

            properties_path = os.path.join(selected_server_path.current, "server.properties")
            if not os.path.exists(properties_path):
                page.overlay.append(ft.SnackBar(ft.Text(f"在 '{os.path.basename(selected_server_path.current)}' 中未找到 server.properties 文件。"), open=True))
                page.update()
                return

            properties = {}
            try:
                with open(properties_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            if '=' in line:
                                key, value = line.split('=', 1)
                                properties[key.strip()] = value.strip()
            except Exception as ex:
                page.overlay.append(ft.SnackBar(ft.Text(f"读取配置失败: {ex}"), open=True))
                page.update()
                return

            property_fields = [
                ft.TextField(label=key, value=value, data=key, dense=True) for key, value in properties.items()
            ]

            # Define the dialog here so it's in scope for the handlers
            dialog = ft.AlertDialog(modal=True)

            def save_properties(e_save):
                new_properties = {field.data: field.value for field in property_fields}
                try:
                    with open(properties_path, 'r', encoding='utf-8') as f: lines = f.readlines()
                    new_lines = []
                    for line in lines:
                        stripped_line = line.strip()
                        if stripped_line and not stripped_line.startswith('#') and '=' in stripped_line:
                            key = stripped_line.split('=', 1)[0].strip()
                            if key in new_properties: new_lines.append(f"{key}={new_properties[key]}\n")
                            else: new_lines.append(line)
                        else: new_lines.append(line)
                    with open(properties_path, 'w', encoding='utf-8') as f: f.writelines(new_lines)

                    dialog.open = False
                    page.overlay.append(ft.SnackBar(ft.Text("配置已成功保存!"), open=True))
                    page.update()
                except Exception as ex:
                    page.overlay.append(ft.SnackBar(ft.Text(f"保存配置失败: {ex}"), open=True))
                    page.update()

            def close_dialog(e_close):
                dialog.open = False
                page.update()

            dialog.title=ft.Text(f"编辑 {os.path.basename(selected_server_path.current)}/server.properties")
            dialog.content=ft.Column(
                controls=property_fields,
                scroll=ft.ScrollMode.ADAPTIVE,
                height=page.window.height * 0.6 if page.window.height else 500,
                spacing=5
            )
            dialog.actions=[
                ft.TextButton("取消", on_click=close_dialog),
                ft.FilledButton("保存", on_click=save_properties),
            ]
            dialog.actions_alignment=ft.MainAxisAlignment.END
            
            page.overlay.append(dialog)
            dialog.open = True
            page.update()

        configure_button.on_click = open_properties_editor

        def delete_server_action(e):
            dialog = e.control.data # Get the dialog from the button
            path_to_delete = selected_server_path.current
            if not path_to_delete or not os.path.exists(path_to_delete):
                page.overlay.append(ft.SnackBar(ft.Text("错误: 服务器路径无效或已不存在。"), open=True))
                dialog.open = False
                page.update()
                return
            
            try:
                shutil.rmtree(path_to_delete)
                page.overlay.append(ft.SnackBar(ft.Text(f"服务器 '{os.path.basename(path_to_delete)}' 已被删除。"), open=True))
                update_server_list() # This will refresh the list and deselect
            except Exception as ex:
                page.overlay.append(ft.SnackBar(ft.Text(f"删除失败: {ex}"), open=True))
            
            dialog.open = False
            page.update()

        def confirm_delete_server(e):
            if not selected_server_path.current:
                return # Should be disabled anyway, but as a safeguard
            
            if server_process is not None:
                page.overlay.append(ft.SnackBar(ft.Text("无法删除正在运行的服务器。请先停止它。", bgcolor=ft.Colors.RED), open=True))
                page.update()
                return

            server_name = os.path.basename(selected_server_path.current)
            
            # Define dialog here to pass it to the action button
            confirm_dialog = ft.AlertDialog(modal=True)
            
            confirm_button = ft.FilledButton("确认删除", on_click=delete_server_action, style=ft.ButtonStyle(bgcolor=ft.Colors.RED), data=confirm_dialog)
            
            confirm_dialog.title = ft.Text("确认删除服务器")
            confirm_dialog.content = ft.Text(f"您确定要永久删除服务器 '{server_name}' 吗？\n此操作无法撤销，所有文件都将被删除。")
            confirm_dialog.actions = [
                ft.TextButton("取消", on_click=lambda _: (setattr(confirm_dialog, 'open', False), page.update())),
                confirm_button,
            ]
            confirm_dialog.actions_alignment = ft.MainAxisAlignment.END

            page.overlay.append(confirm_dialog)
            confirm_dialog.open = True
            page.update()

        delete_server_button.on_click = confirm_delete_server

        def on_server_selected(e):
            server_name = e.control.value
            if server_name:
                selected_server_path.current = os.path.join(SERVERS_ROOT_DIR, server_name)
                is_running = server_process is not None
                start_button.disabled = is_running
                configure_button.disabled = is_running
                delete_server_button.disabled = is_running
                console_output.controls.append(create_console_text(f"已选择服务器: {server_name}", color=ft.Colors.BLUE))
            else:
                selected_server_path.current = None
                start_button.disabled = True
                configure_button.disabled = True
                delete_server_button.disabled = True
            page.update()

        server_selector_dropdown = ft.Dropdown(
            label="选择一个服务器实例",
            expand=True,
            options=[],
            on_change=on_server_selected,
            border_radius=ft.border_radius.all(8)
        )

        def update_server_list(e=None):
            try:
                server_dirs = [d for d in os.listdir(SERVERS_ROOT_DIR) if os.path.isdir(os.path.join(SERVERS_ROOT_DIR, d))]
                server_selector_dropdown.options = [ft.dropdown.Option(d) for d in server_dirs]
                
                current_selection = os.path.basename(selected_server_path.current) if selected_server_path.current else None
                if current_selection and current_selection in server_dirs:
                    server_selector_dropdown.value = current_selection
                else:
                    selected_server_path.current = None
                    server_selector_dropdown.value = None
                    start_button.disabled = True
                    configure_button.disabled = True
                    delete_server_button.disabled = True
                
                if e:
                    page.overlay.append(ft.SnackBar(ft.Text("服务器列表已刷新"), duration=2000, open=True))

            except Exception as ex:
                page.overlay.append(ft.SnackBar(ft.Text(f"刷新列表失败: {ex}"), open=True))
            page.update()
        
        update_server_list()

        view = ft.Column(
            [
                ft.Text("主页控制台", style=ft.TextThemeStyle.HEADLINE_SMALL),
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Stack([
                                    ft.Container(
                                        content=console_output,
                                        border=ft.border.all(1, ft.Colors.with_opacity(0.1, ft.Colors.ON_SURFACE)),
                                        border_radius=ft.border_radius.all(10),
                                        padding=15,
                                        expand=True,
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.COPY_ALL_ROUNDED,
                                        tooltip="复制全部输出",
                                        on_click=copy_console_output,
                                        right=10,
                                        top=10,
                                    )
                                ], expand=True),
                                ft.Row([command_input, send_button]),
                            ],
                            expand=3,
                        ),
                        ft.Column(
                            [
                                SettingsCard("服务器控制", [
                                    ft.Row([
                                        server_selector_dropdown,
                                        ft.IconButton(icon=ft.Icons.REFRESH_ROUNDED, on_click=update_server_list, tooltip="刷新列表"),
                                        delete_server_button,
                                    ]),
                                    server_status_text,
                                    player_count_text,
                                    ft.Row([start_button, stop_button, restart_button, configure_button], spacing=10),
                                ]),
                                SettingsCard("性能监控", [
                                    cpu_text, cpu_progress,
                                    ram_text, ram_progress,
                                ]),
                            ],
                            expand=2,
                            spacing=10
                        ),
                    ],
                    expand=True,
                ),
            ],
            expand=True,
            spacing=10
        )
        return view

    def create_settings_view():
        # --- Controls ---
        theme_dropdown = ft.Dropdown(
            label="应用主题",
            value=app_settings.get("theme", "system"),
            options=[
                ft.dropdown.Option("system", "跟随系统"),
                ft.dropdown.Option("light", "亮色模式"),
                ft.dropdown.Option("dark", "暗色模式"),
            ]
        )
        java_path_field = ft.TextField(
            label="Java 可执行文件路径 (留空则使用系统默认)",
            value=app_settings.get("java_path", ""),
            hint_text="例如: C:\\Program Files\\Java\\jdk-17\\bin\\java.exe",
            expand=True
        )
        jvm_args_field = ft.TextField(
            label="默认 JVM 参数",
            value=app_settings.get("jvm_args", "-Xmx1024M -Xms1024M")
        )
        
        PREDEFINED_COLORS = {
            "Blue Grey": ft.Colors.BLUE_GREY, "Blue": ft.Colors.BLUE, "Red": ft.Colors.RED,
            "Green": ft.Colors.GREEN, "Purple": ft.Colors.PURPLE, "Orange": ft.Colors.ORANGE,
            "Teal": ft.Colors.TEAL, "Pink": ft.Colors.PINK,
        }
        selected_color = ft.Ref[str]()
        selected_color.current = app_settings.get("primary_color", ft.Colors.BLUE_GREY)

        def color_option_clicked(e):
            selected_color.current = e.control.data
            for control in color_options.controls:
                if isinstance(control, ft.Container):
                    control.border = ft.border.all(3, ft.Colors.OUTLINE) if control.data == selected_color.current else None
            # Use page.update() for a more reliable redraw of the selection border
            page.update()

        color_options = ft.GridView(expand=False, max_extent=50, spacing=10, run_spacing=10, height=120)
        for color_name, color_value in PREDEFINED_COLORS.items():
            color_options.controls.append(
                ft.Container(
                    width=40, height=40, bgcolor=color_value,
                    border_radius=30,
                    data=color_value,
                    on_click=color_option_clicked,
                    border=ft.border.all(3, ft.Colors.OUTLINE) if color_value == selected_color.current else None
                )
            )

        # --- Handlers ---
        def auto_detect_java(e):
            found_paths = find_all_java_executables()

            if not found_paths:
                page.overlay.append(ft.SnackBar(ft.Text("未在标准目录中找到 Java。"), open=True))
                page.update()
                return

            if len(found_paths) == 1:
                java_path_field.value = found_paths[0]
                page.overlay.append(ft.SnackBar(ft.Text(f"成功找到 Java: {found_paths[0]}"), open=True))
                page.update()
                return

            # More than one found, show a dialog
            java_radiogroup_ref = ft.Ref[ft.RadioGroup]()

            def select_java_version(e_select):
                if java_radiogroup_ref.current and java_radiogroup_ref.current.value:
                    java_path_field.value = java_radiogroup_ref.current.value
                java_selection_dialog.open = False
                page.update()

            def close_dialog(e_close):
                java_selection_dialog.open = False
                page.update()

            java_selection_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("找到多个 Java 版本"),
                content=ft.Container(
                    ft.RadioGroup(
                        ref=java_radiogroup_ref,
                        value=java_path_field.value or found_paths[0], # Set initial selection
                        content=ft.Column(
                            [ft.Radio(value=path, label=path) for path in found_paths],
                            scroll=ft.ScrollMode.ADAPTIVE, spacing=5
                        )
                    ),
                    height=300,
                    width=600,
                    padding=ft.padding.only(top=10)
                ),
                actions=[
                    ft.TextButton("取消", on_click=close_dialog),
                    ft.FilledButton("选择", on_click=select_java_version)
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            
            page.overlay.append(java_selection_dialog)
            java_selection_dialog.open = True
            page.update()

        def save_app_settings(e):
            app_settings["theme"] = theme_dropdown.value or "system"
            app_settings["primary_color"] = selected_color.current or ft.Colors.BLUE_GREY
            app_settings["java_path"] = java_path_field.value or ""
            app_settings["jvm_args"] = jvm_args_field.value or "-Xmx1024M -Xms1024M"
            app_settings["download_source"] = download_source_dropdown.value or "Official"
            save_settings()
            
            page.theme_mode = str_to_theme_mode(app_settings.get("theme", "system"))
            primary_color = app_settings.get("primary_color", ft.Colors.BLUE_GREY)
            page.theme = ft.Theme(color_scheme_seed=primary_color, font_family="Roboto")
            page.dark_theme = ft.Theme(color_scheme_seed=primary_color, font_family="Roboto")
            
            page.overlay.append(ft.SnackBar(ft.Text("设置已保存! 部分主题更改可能需要重启应用。"), open=True))
            page.update()

        save_button = ft.FilledButton("保存设置", icon=ft.Icons.SAVE_ROUNDED, on_click=save_app_settings)

        download_source_dropdown = ft.Dropdown(
            label="下载源 (Download Source)",
            value=app_settings.get("download_source", "Official"),
            options=[
                ft.dropdown.Option("Official", "官方源"),
                ft.dropdown.Option("BMCLAPI (China Mirror)", "BMCLAPI (中国镜像)"),
            ],
            tooltip="为核心和插件选择下载来源。如果在中国大陆遇到速度问题，请尝试 BMCLAPI。"
        )

        # --- Layout ---
        return ft.Column([
            ft.Text("应用设置", style=ft.TextThemeStyle.HEADLINE_SMALL),
            SettingsCard("外观", [
                theme_dropdown,
                ft.Text("主题颜色", style=ft.TextThemeStyle.BODY_LARGE),
                color_options,
            ]),
            SettingsCard("Java 环境", [
                ft.Row([
                    java_path_field,
                    ft.FilledButton("自动检测", icon=ft.Icons.SEARCH, on_click=auto_detect_java)
                ], alignment=ft.MainAxisAlignment.START),
                jvm_args_field
            ]),
            SettingsCard("网络设置", [
                download_source_dropdown
            ]),
            save_button
        ], spacing=10, expand=True)

    def create_player_management_view():
        
        # --- Helper Functions for JSON file-based lists (Banned, OP, Whitelist) ---
        def get_json_list_path(list_type: str) -> Optional[str]:
            if not selected_server_path.current: return None
            filename_map = {
                "banned": "banned-players.json",
                "ops": "ops.json",
                "whitelist": "whitelist.json"
            }
            return os.path.join(selected_server_path.current, filename_map.get(list_type, ""))

        def load_json_list(list_type: str) -> List[dict[str, Any]]:
            path = get_json_list_path(list_type)
            if not path or not os.path.exists(path): return []
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except (json.JSONDecodeError, IOError):
                return []

        def save_json_list(list_type: str, data: List[dict[str, Any]]):
            path = get_json_list_path(list_type)
            if not path: return
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
            except IOError as e:
                page.overlay.append(ft.SnackBar(ft.Text(f"保存文件失败: {e}"), open=True))
                page.update()

        # --- Online Player Tab Content ---
        def create_online_players_tab():
            player_list_view = ft.ListView(expand=True, spacing=5)

            def execute_player_command(command: str, player: str):
                full_command = f"{command} {player}"
                if server_process and server_process.stdin:
                    try:
                        server_process.stdin.write(full_command + "\n")
                        server_process.stdin.flush()
                        console_output.controls.append(create_console_text(f"> {full_command}", color=ft.Colors.CYAN))
                        page.overlay.append(ft.SnackBar(ft.Text(f"命令 '{full_command}' 已发送。"), open=True))
                    except Exception as ex:
                        page.overlay.append(ft.SnackBar(ft.Text(f"命令发送失败: {ex}"), open=True))
                else:
                    page.overlay.append(ft.SnackBar(ft.Text("服务器未运行或无法发送命令。"), open=True))
                page.update()

            def build_online_player_list():
                player_list_view.controls.clear()
                if not server_process or server_process.poll() is not None:
                    player_list_view.controls.append(ft.Text("服务器未运行。"))
                elif not online_players.current:
                    player_list_view.controls.append(ft.Text("当前没有玩家在线。"))
                else:
                    for player_name in online_players.current:
                        player_list_view.controls.append(
                            ft.ListTile(
                                leading=ft.Icon(ft.Icons.PERSON), title=ft.Text(player_name, weight=ft.FontWeight.BOLD),
                                trailing=ft.Row([
                                    ft.IconButton(icon=ft.Icons.DO_NOT_DISTURB_ON, tooltip="踢出", on_click=lambda _, p=player_name: execute_player_command("kick", p), icon_color=ft.Colors.ORANGE),
                                    ft.IconButton(icon=ft.Icons.GAVEL, tooltip="封禁", on_click=lambda _, p=player_name: execute_player_command("ban", p), icon_color=ft.Colors.RED),
                                    ft.IconButton(icon=ft.Icons.STAR, tooltip="设为OP", on_click=lambda _, p=player_name: execute_player_command("op", p), icon_color=ft.Colors.AMBER),
                                    ft.IconButton(icon=ft.Icons.REMOVE_MODERATOR, tooltip="取消OP", on_click=lambda _, p=player_name: execute_player_command("deop", p), icon_color=ft.Colors.BLUE_GREY),
                                ])
                            )
                        )
                page.update()

            def refresh_online_list(e=None):
                if server_process and server_process.stdin:
                    try:
                        server_process.stdin.write("list\n")
                        server_process.stdin.flush()
                    except (IOError, AttributeError): pass
                build_online_player_list()

            online_tab_content = ft.Column([
                ft.Row([ft.Text("实时在线玩家", style=ft.TextThemeStyle.TITLE_MEDIUM), ft.IconButton(icon=ft.Icons.REFRESH, on_click=refresh_online_list, tooltip="刷新列表")], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                player_list_view
            ], expand=True)
            online_tab_content.data = build_online_player_list
            return online_tab_content

        # --- Offline (JSON) Player Tab Content ---
        def create_json_list_tab(list_type: str, title: str):
            list_view = ft.ListView(expand=True, spacing=5)
            add_player_textfield = ft.TextField(label="输入玩家名称...", expand=True)
            add_player_button = ft.IconButton(icon=ft.Icons.ADD, tooltip="添加玩家")

            def build_list():
                list_view.controls.clear()
                if not selected_server_path.current:
                    list_view.controls.append(ft.Text("请先在主页选择一个服务器实例。"))
                else:
                    data = load_json_list(list_type)
                    if not data:
                        list_view.controls.append(ft.Text("列表为空。"))
                    for item in data:
                        player_name = item.get("name", "未知玩家")
                        player_uuid = item.get("uuid")
                        list_view.controls.append(
                            ft.ListTile(
                                leading=ft.Icon(ft.Icons.PERSON_OFF), title=ft.Text(player_name),
                                trailing=ft.IconButton(icon=ft.Icons.DELETE_FOREVER, tooltip="移除", data=player_uuid, on_click=remove_player, icon_color=ft.Colors.RED)
                            )
                        )
                page.update()

            def add_player_thread(name: str):
                page.overlay.append(ft.SnackBar(ft.Text(f"正在查找玩家 '{name}'..."), open=True, duration=4000))
                page.update()

                player_data = get_player_uuid(name)

                if not player_data:
                    page.overlay.append(ft.SnackBar(ft.Text(f"玩家 '{name}' 未找到或 Mojang API 出错。"), open=True))
                    add_player_button.disabled = False
                    page.update()
                    return

                corrected_name = player_data["name"]
                player_uuid = player_data["id"]

                data = load_json_list(list_type)
                if any(p.get("uuid", "").replace("-", "") == player_uuid for p in data):
                    page.overlay.append(ft.SnackBar(ft.Text(f"玩家 '{corrected_name}' 已在列表中。"), open=True))
                    add_player_button.disabled = False
                    page.update()
                    return

                new_entry = {"uuid": player_uuid, "name": corrected_name}
                if list_type == "banned":
                    new_entry.update({"created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %z"), "source": "Server Panel", "expires": "forever", "reason": "Banned via Panel"})
                
                data.append(new_entry)
                save_json_list(list_type, data)
                add_player_textfield.value = ""
                build_list()
                page.overlay.append(ft.SnackBar(ft.Text(f"玩家 '{corrected_name}' 已添加。服务器可能需要重载才能生效。"), open=True))
                add_player_button.disabled = False
                page.update()

            def add_player_handler(e):
                player_name = add_player_textfield.value
                if not player_name: return
                add_player_button.disabled = True
                page.update()
                page.run_thread(add_player_thread, player_name)
            
            add_player_button.on_click = add_player_handler

            def remove_player(e):
                player_uuid = e.control.data
                data = load_json_list(list_type)
                data = [p for p in data if p.get("uuid") != player_uuid]
                save_json_list(list_type, data)
                build_list()
                page.overlay.append(ft.SnackBar(ft.Text("玩家已移除。服务器可能需要重载才能生效。"), open=True))
                page.update()

            json_tab_content = ft.Column([
                ft.Row([ft.Text(title, style=ft.TextThemeStyle.TITLE_MEDIUM), ft.IconButton(icon=ft.Icons.REFRESH, on_click=lambda e: build_list(), tooltip="从文件重新加载")]),
                ft.Row([add_player_textfield, add_player_button]),
                ft.Divider(),
                list_view
            ], expand=True)
            json_tab_content.data = build_list
            return json_tab_content

        # --- Main View Construction ---
        online_tab = create_online_players_tab()
        banned_tab = create_json_list_tab("banned", "封禁列表 (banned-players.json)")
        ops_tab = create_json_list_tab("ops", "管理员 (ops.json)")
        whitelist_tab = create_json_list_tab("whitelist", "白名单 (whitelist.json)")

        all_tabs = [online_tab, banned_tab, ops_tab, whitelist_tab]

        def on_tab_change(e):
            selected_tab_content = all_tabs[e.control.selected_index]
            if hasattr(selected_tab_content, 'data') and callable(selected_tab_content.data):
                selected_tab_content.data()

        tabs_control = ft.Tabs(
            selected_index=0,
            on_change=on_tab_change,
            tabs=[
                ft.Tab(text="在线玩家", content=online_tab),
                ft.Tab(text="封禁列表", content=banned_tab),
                ft.Tab(text="OP列表", content=ops_tab),
                ft.Tab(text="白名单", content=whitelist_tab),
            ],
            expand=True,
        )

        def refresh_all_views():
            # Guard against the control not being fully initialized on first load
            if not tabs_control.uid:
                return
            on_tab_change(ft.ControlEvent(target=tabs_control.uid, name="change", data=str(tabs_control.selected_index), control=tabs_control, page=page))

        view_container = ft.Column([
            ft.Text("玩家管理", style=ft.TextThemeStyle.HEADLINE_SMALL),
            tabs_control
        ], expand=True)
        
        view_container.data = refresh_all_views
        return view_container

    def create_file_manager_view():
        file_list_view = ft.ListView(expand=True, spacing=5)
        file_details_view = ft.Column(expand=1, spacing=10, key=str(uuid.uuid4()))
        current_path_text = ft.Text(weight=ft.FontWeight.BOLD)
        base_path = os.path.abspath(SERVERS_ROOT_DIR)

        editor_textfield = ft.TextField(
            multiline=True, expand=True, min_lines=20,
            border=ft.InputBorder.OUTLINE,
            border_radius=ft.border_radius.all(5)
        )
        current_editing_path = ft.Ref[Optional[str]]()
        
        # Define dialog here to be accessible by its handlers
        edit_dialog = ft.AlertDialog(modal=True)

        def save_file(e):
            if not current_editing_path.current: return
            try:
                with open(current_editing_path.current, 'w', encoding='utf-8') as f: f.write(editor_textfield.value or "")
                edit_dialog.open = False
                page.overlay.append(ft.SnackBar(ft.Text(f"文件 '{os.path.basename(current_editing_path.current)}' 已保存!"), open=True))
                page.update()
            except Exception as ex:
                page.overlay.append(ft.SnackBar(ft.Text(f"保存失败: {ex}"), open=True))
                page.update()

        def close_edit_dialog(e):
            edit_dialog.open = False
            page.update()

        def open_editor(file_path):
            try:
                if os.path.getsize(file_path) > 5 * 1024 * 1024:
                    page.overlay.append(ft.SnackBar(ft.Text("文件太大 (>5MB)，无法在应用内编辑。"), open=True))
                    page.update()
                    return
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f: content = f.read()
                editor_textfield.value = content
                current_editing_path.current = file_path
                
                edit_dialog.title = ft.Text(f"编辑: {os.path.relpath(file_path, base_path)}")
                edit_dialog.content = ft.Container(editor_textfield, width=800, height=600, padding=5)
                edit_dialog.actions = [
                    ft.TextButton("取消", on_click=close_edit_dialog),
                    ft.FilledButton("保存", icon=ft.Icons.SAVE, on_click=save_file),
                ]
                edit_dialog.actions_alignment=ft.MainAxisAlignment.END

                if edit_dialog not in page.overlay:
                    page.overlay.append(edit_dialog)
                
                edit_dialog.open = True
                page.update()
            except Exception as e:
                page.overlay.append(ft.SnackBar(ft.Text(f"无法打开或读取文件: {e}"), open=True))
                page.update()

        def show_item_details(item_path):
            file_details_view.controls.clear()
            is_dir = os.path.isdir(item_path)
            try:
                stat = os.stat(item_path)
                modified_time = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                
                # Use a temporary list to build controls
                details_controls_list: list[ft.Control] = [
                    ft.Text("详情", style=ft.TextThemeStyle.TITLE_MEDIUM),
                    ft.Text(f"名称: {os.path.basename(item_path)}", selectable=True),
                    ft.Text(f"修改日期: {modified_time}", selectable=True),
                ]
                
                if is_dir:
                    details_controls_list.append(ft.Container(
                        ft.FilledButton(
                            "在文件资源管理器中打开", icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                            on_click=lambda _, p=item_path: os.startfile(p),
                            tooltip="在本地文件浏览器中打开此文件夹"
                        ), margin=ft.margin.only(top=10)
                    ))
                    details_controls_list.append(ft.Divider(height=20))
                    details_controls_list.append(ft.Text("内容预览:", weight=ft.FontWeight.BOLD))
                    try:
                        content_preview = ft.Column(spacing=2)
                        items = os.listdir(item_path)
                        if not items:
                            content_preview.controls.append(ft.Text("  (文件夹为空)"))
                        else:
                            for i, item_name in enumerate(items[:15]):
                                icon = ft.Icons.FOLDER_SHARED_OUTLINED if os.path.isdir(os.path.join(item_path, item_name)) else ft.Icons.DESCRIPTION_OUTLINED
                                content_preview.controls.append(ft.Row([ft.Icon(icon, size=16), ft.Text(item_name)]))
                            if len(items) > 15:
                                content_preview.controls.append(ft.Text("  ..."))
                        details_controls_list.append(content_preview)
                    except Exception as preview_e:
                        details_controls_list.append(ft.Text(f"无法预览内容: {preview_e}", color=ft.Colors.RED))

                else:
                    size_bytes = stat.st_size
                    size_str = f"{size_bytes} B" if size_bytes < 1024 else f"{size_bytes/1024:.2f} KB" if size_bytes < 1024*1024 else f"{size_bytes/(1024*1024):.2f} MB"
                    details_controls_list.insert(2, ft.Text(f"大小: {size_str}", selectable=True))
                    editable_extensions = ['.txt', '.yml', '.yaml', '.json', '.properties', '.log', '.bat', '.sh', '.md', '.ini']
                    if os.path.splitext(item_path)[1].lower() in editable_extensions:
                        details_controls_list.append(ft.Container(
                            ft.FilledButton(
                                "编辑文件", icon=ft.Icons.EDIT_DOCUMENT,
                                on_click=lambda _, p=item_path: open_editor(p),
                                tooltip="在应用内编辑此文本文件"
                            ), margin=ft.margin.only(top=10)
                        ))
                
                # Assign the fully built list to the view's controls
                file_details_view.controls = details_controls_list
            except Exception as e:
                file_details_view.controls.append(ft.Text(f"无法读取详情: {e}", color=ft.Colors.RED))
            page.update()

        def create_tile_hover_style():
            def on_hover(e):
                e.control.bgcolor = ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE) if e.data == "true" else ft.Colors.TRANSPARENT
                e.control.update()
            return on_hover

        def on_file_list_click(e):
            item_path = e.control.data
            if os.path.isdir(item_path):
                list_directory(item_path)
            else:
                show_item_details(item_path)

        def list_directory(path):
            current_path_text.value = f"当前: {os.path.relpath(path, os.path.dirname(base_path))}"
            file_list_view.controls.clear()
            file_details_view.controls.clear()

            def create_list_item(text, icon, on_click_handler, data=None, is_dir=False, weight=None):
                return ft.Container(
                    content=ft.ListTile(title=ft.Text(text, weight=weight), leading=ft.Icon(icon), data=data, on_click=on_click_handler),
                    border_radius=ft.border_radius.all(8),
                    on_hover=create_tile_hover_style(),
                    on_click=on_click_handler,
                    data=data
                )

            if os.path.abspath(path) != base_path:
                file_list_view.controls.append(create_list_item(
                    ".. 返回上一级", ft.Icons.ARROW_UPWARD_ROUNDED, lambda e: list_directory(os.path.dirname(path))
                ))
            
            try:
                dirs, files = [], []
                for item in os.listdir(path):
                    (dirs if os.path.isdir(os.path.join(path, item)) else files).append(item)
                
                for item in sorted(dirs):
                    item_path = os.path.join(path, item)
                    file_list_view.controls.append(create_list_item(
                        item, ft.Icons.FOLDER_ROUNDED, on_file_list_click, data=item_path, is_dir=True, weight=ft.FontWeight.BOLD
                    ))

                for item in sorted(files):
                    item_path = os.path.join(path, item)
                    file_list_view.controls.append(create_list_item(
                        item, ft.Icons.DESCRIPTION_OUTLINED, on_file_list_click, data=item_path
                    ))
            except Exception as e:
                file_list_view.controls.append(ft.Text(f"无法访问目录: {e}", color=ft.Colors.RED))
            page.update()

        list_directory(base_path)
        return ft.Column([
            ft.Text("文件管理", style=ft.TextThemeStyle.HEADLINE_SMALL),
            current_path_text,
            ft.Row([
                ft.Container(
                    ft.Column([
                        ft.Text("文件列表", style=ft.TextThemeStyle.TITLE_MEDIUM),
                        ft.Divider(height=1),
                        ft.Container(content=file_list_view, expand=True, padding=ft.padding.only(top=10))
                    ]),
                    expand=1,
                    padding=15,
                    border_radius=ft.border_radius.all(10),
                    border=ft.border.all(1, ft.Colors.with_opacity(0.1, ft.Colors.ON_SURFACE)),
                    bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.ON_SURFACE),
                ),
                SettingsCard("文件详情", [file_details_view], expand=1),
            ], expand=True, spacing=20, vertical_alignment=ft.CrossAxisAlignment.START)
        ], spacing=10, expand=True)

    def create_plugin_manager_view():
        installed_plugins_list = ft.ListView(expand=True, spacing=5)
        
        plugin_source_dropdown = ft.Dropdown(
            label="插件源",
            value="Modrinth",
            options=[
                ft.dropdown.Option("Modrinth"),
                ft.dropdown.Option("PaperMC"),
            ],
            width=150,
            tooltip="选择搜索插件的来源"
        )
        search_input = ft.TextField(label="搜索插件...", expand=True)
        search_results_list = ft.ListView(expand=True, spacing=8)
        
        plugin_details_view = ft.Column(visible=False, spacing=10, scroll=ft.ScrollMode.ADAPTIVE)
        plugin_versions_dropdown = ft.Dropdown(label="选择插件版本", expand=True)
        download_button = ft.FilledButton("下载到服务器", icon=ft.Icons.DOWNLOAD, disabled=True)
        download_progress = ft.ProgressBar(value=0, visible=False)
        
        selected_project = ft.Ref[dict[str, Any]]()

        def update_installed_plugins_list(e=None):
            installed_plugins_list.controls.clear()
            if not selected_server_path.current:
                installed_plugins_list.controls.append(ft.Text("请先在主页选择服务器。"))
            else:
                plugins_dir = os.path.join(selected_server_path.current, "plugins")
                if not os.path.isdir(plugins_dir):
                    os.makedirs(plugins_dir, exist_ok=True)
                
                plugin_files = [f for f in os.listdir(plugins_dir) if f.endswith('.jar')]
                if not plugin_files:
                    installed_plugins_list.controls.append(ft.Text("没有已安装的插件。"))
                else:
                    for plugin_file in plugin_files:
                        installed_plugins_list.controls.append(
                            ft.ListTile(leading=ft.Icon(ft.Icons.EXTENSION), title=ft.Text(plugin_file))
                        )
            page.update()

        def download_plugin_thread(version_data: dict[str, Any]):
            if not selected_server_path.current:
                page.overlay.append(ft.SnackBar(ft.Text("错误: 未选择服务器。"), open=True))
                page.update()
                return
            try:
                download_url = version_data['url']
                filename = version_data['filename']
                plugins_dir = os.path.join(selected_server_path.current, "plugins")
                os.makedirs(plugins_dir, exist_ok=True)
                final_path = os.path.join(plugins_dir, filename)

                download_progress.visible = True
                download_progress.value = None
                page.update()

                with requests.get(download_url, stream=True, timeout=300, headers=REQUESTS_HEADERS) as r:
                    r.raise_for_status()
                    total_size = int(r.headers.get('content-length', 0))
                    bytes_downloaded = 0
                    with open(final_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                            bytes_downloaded += len(chunk)
                            if total_size > 0:
                                download_progress.value = bytes_downloaded / total_size
                                page.update()
                
                page.overlay.append(ft.SnackBar(ft.Text(f"插件 '{filename}' 下载成功!"), open=True))
                completion_sound.play()
                update_installed_plugins_list()
            except Exception as ex:
                page.overlay.append(ft.SnackBar(ft.Text(f"下载失败: {ex}"), open=True))
            finally:
                download_progress.visible = False
                download_button.disabled = False
                page.update()

        def on_download_click(e):
            if plugin_versions_dropdown.value:
                download_button.disabled = True
                page.run_thread(download_plugin_thread, json.loads(plugin_versions_dropdown.value))

        def fetch_plugin_versions_thread_modrinth(project_id: str, status_text_control: ft.Text):
            plugin_versions_dropdown.options = []
            plugin_versions_dropdown.value = None
            plugin_versions_dropdown.disabled = True
            download_button.disabled = True
            page.update()

            server_game_version = get_server_game_version(selected_server_path.current)
            
            params = {'loaders': '["paper", "spigot", "bukkit"]'}
            if server_game_version:
                params['game_versions'] = f'["{server_game_version}"]'
                status_text_control.value = f"筛选版本: Minecraft {server_game_version}"
            else:
                status_text_control.value = "未自动检测到游戏版本，显示所有版本。"
            page.update()

            try:
                url = f"{get_api_base_url('modrinth')}/v2/project/{project_id}/version"
                r = requests.get(url, params=params, timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                r.raise_for_status()
                versions = r.json()

                if not versions:
                    if server_game_version:
                        status_text_control.value = f"未找到适用于 Minecraft {server_game_version} 的版本。"
                    else:
                        status_text_control.value = "未找到任何兼容的插件版本。"
                    page.update()
                    return

                for v in versions:
                    primary_file = next((f for f in v['files'] if f['primary']), v['files'][0])
                    option_data = json.dumps({'url': primary_file['url'], 'filename': primary_file['filename']})
                    plugin_versions_dropdown.options.append(
                        ft.dropdown.Option(key=option_data, text=f"{v['name']} ({v['version_number']})")
                    )
                plugin_versions_dropdown.disabled = False
            except Exception as ex:
                status_text_control.value = f"获取版本失败: {ex}"
                status_text_control.color = ft.Colors.RED
            page.update()

        def fetch_plugin_versions_thread_papermc(project_data: dict, status_text_control: ft.Text):
            plugin_versions_dropdown.options = []
            plugin_versions_dropdown.value = None
            plugin_versions_dropdown.disabled = True
            download_button.disabled = True
            page.update()

            server_game_version = get_server_game_version(selected_server_path.current)
            
            params = {}
            if server_game_version:
                # Hangar API uses a list format for game versions
                params['gameVersions'] = server_game_version
                status_text_control.value = f"筛选版本: Minecraft {server_game_version}"
            else:
                status_text_control.value = "未自动检测到游戏版本，显示所有版本。"
            page.update()

            try:
                author = project_data['author']
                slug = project_data['slug']
                url = f"{get_api_base_url('hangar')}/projects/{author}/{slug}/versions"
                r = requests.get(url, params=params, timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                r.raise_for_status()
                versions_data = r.json()
                versions = versions_data.get('result', [])

                if not versions:
                    if server_game_version:
                        status_text_control.value = f"未找到适用于 Minecraft {server_game_version} 的版本。"
                    else:
                        status_text_control.value = "未找到任何兼容的插件版本。"
                    page.update()
                    return

                for v in versions:
                    # Find the PAPER download type, as it's the most common for plugins
                    download_info = v.get('downloads', {}).get('PAPER')
                    if not download_info:
                        continue # Skip versions with no paper download

                    download_url = f"{get_api_base_url('hangar')}/projects/{author}/{slug}/versions/{v['name']}/PAPER/download"
                    
                    option_data = json.dumps({
                        'url': download_url, 
                        'filename': download_info.get('name', 'plugin.jar')
                    })
                    plugin_versions_dropdown.options.append(
                        ft.dropdown.Option(key=option_data, text=f"{v['name']}")
                    )
                plugin_versions_dropdown.disabled = False
            except Exception as ex:
                status_text_control.value = f"获取版本失败: {type(ex).__name__}: {ex}"
                status_text_control.color = ft.Colors.RED
            page.update()

        def on_search_result_click(e):
            project = e.control.data
            selected_project.current = project
            
            version_filter_status = ft.Text(italic=True)
            
            plugin_details_view.controls = [
                ft.Text(project['title'], style=ft.TextThemeStyle.TITLE_LARGE),
                ft.Text(project['author'], style=ft.TextThemeStyle.BODY_SMALL, italic=True),
                ft.Text(project['description']),
                ft.Divider(),
                version_filter_status,
                plugin_versions_dropdown,
                ft.Row([download_button]),
                download_progress
            ]
            plugin_details_view.visible = True
            
            source = project.get('source', 'modrinth')
            if source == 'modrinth':
                page.run_thread(fetch_plugin_versions_thread_modrinth, project['project_id'], version_filter_status)
            elif source == 'papermc':
                page.run_thread(fetch_plugin_versions_thread_papermc, project, version_filter_status)
                
            page.update()

        def search_modrinth_thread(query: str):
            search_results_list.controls.clear()
            search_results_list.controls.append(ft.ProgressRing())
            page.update()

            try:
                url = f"{get_api_base_url('modrinth')}/v2/search"
                params = {'query': query, 'facets': '[["project_type:plugin"]]', 'limit': 20}
                r = requests.get(url, params=params, timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                r.raise_for_status()
                data = r.json()
                
                search_results_list.controls.clear()
                if not data['hits']:
                    search_results_list.controls.append(ft.Text("未找到结果。"))
                for hit in data['hits']:
                    hit['source'] = 'modrinth' # Add source identifier
                    search_results_list.controls.append(
                        ft.ListTile(
                            leading=ft.Image(src=hit['icon_url'], width=48, height=48, fit=ft.ImageFit.CONTAIN, border_radius=5),
                            title=ft.Text(hit['title'], weight=ft.FontWeight.BOLD),
                            subtitle=ft.Text(hit['description'], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                            on_click=on_search_result_click,
                            data=hit
                        )
                    )
            except Exception as ex:
                search_results_list.controls.clear()
                search_results_list.controls.append(ft.Text(f"搜索失败: {type(ex).__name__}: {ex}", color=ft.Colors.RED))
            page.update()

        def search_papermc_thread(query: str):
            search_results_list.controls.clear()
            search_results_list.controls.append(ft.ProgressRing())
            page.update()

            try:
                url = f"{get_api_base_url('hangar')}/projects"
                params = {'q': query, 'limit': 20, 'sort': '-stars'} # Sort by most stars
                r = requests.get(url, params=params, timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                r.raise_for_status()
                data = r.json()
                
                search_results_list.controls.clear()
                if not data.get('result'):
                    search_results_list.controls.append(ft.Text("未找到结果。"))
                for hit in data.get('result', []):
                    project_data = {
                        'source': 'papermc',
                        'title': hit.get('name'),
                        'author': hit.get('namespace', {}).get('owner'),
                        'slug': hit.get('name'),
                        'description': hit.get('description', 'No description provided.'),
                        'icon_url': hit.get('avatarUrl')
                    }
                    search_results_list.controls.append(
                        ft.ListTile(
                            leading=ft.Image(src=project_data['icon_url'], width=48, height=48, fit=ft.ImageFit.CONTAIN, border_radius=5),
                            title=ft.Text(project_data['title'], weight=ft.FontWeight.BOLD),
                            subtitle=ft.Text(project_data['description'], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                            on_click=on_search_result_click,
                            data=project_data
                        )
                    )
            except Exception as ex:
                search_results_list.controls.clear()
                search_results_list.controls.append(ft.Text(f"搜索失败: {type(ex).__name__}: {ex}", color=ft.Colors.RED))
            page.update()

        def on_search_click(e):
            if search_input.value:
                source = plugin_source_dropdown.value
                if source == "Modrinth":
                    page.run_thread(search_modrinth_thread, search_input.value)
                elif source == "PaperMC":
                    page.run_thread(search_papermc_thread, search_input.value)

        def on_version_selected(e):
            download_button.disabled = e.control.value is None
            page.update()

        def refresh_all(e=None):
            update_installed_plugins_list()
            search_input.value = ""
            search_results_list.controls.clear()
            plugin_details_view.visible = False
            page.update()

        search_input.on_submit = on_search_click
        plugin_versions_dropdown.on_change = on_version_selected
        download_button.on_click = on_download_click

        view = ft.Column([
            ft.Text("插件管理", style=ft.TextThemeStyle.HEADLINE_SMALL),
            ft.Row([
                SettingsCard("已安装插件", [
                    ft.Row([ft.Text("本地插件", style=ft.TextThemeStyle.TITLE_MEDIUM), ft.IconButton(icon=ft.Icons.REFRESH, on_click=update_installed_plugins_list)]),
                    installed_plugins_list
                ], expand=1),
                SettingsCard("在线搜索", [
                    ft.Row([plugin_source_dropdown, search_input, ft.IconButton(icon=ft.Icons.SEARCH, on_click=on_search_click, tooltip="搜索")], spacing=10),
                    ft.Divider(),
                    ft.Row([
                        ft.Column([search_results_list], expand=1),
                        ft.VerticalDivider(width=10),
                        ft.Column([plugin_details_view], expand=1),
                    ], expand=True)
                ], expand=2)
            ], expand=True, spacing=20)
        ], expand=True)
        
        view.data = refresh_all
        return view

    def create_core_download_view():
        current_server_path = ft.Ref[Optional[str]]()
        server_name_input = ft.TextField(label="为新服务器命名", expand=True, autofocus=True)
        create_server_button = ft.FilledButton("创建并设置核心", icon=ft.Icons.CREATE_NEW_FOLDER)
        download_status_text = ft.Text("", visible=False)
        download_progress = ft.ProgressBar(value=0, width=400, visible=False)
        core_api_data = {}

        version_list_view = ft.ListView(expand=True, spacing=5, auto_scroll=True)
        build_list_view = ft.ListView(expand=True, spacing=5, auto_scroll=True)
        download_button = ft.FilledButton("下载核心", icon=ft.Icons.DOWNLOAD, disabled=True)

        def update_status(message, color=ft.Colors.BLACK, show_progress=False):
            download_status_text.value = message
            download_status_text.color = color
            download_status_text.visible = True
            download_progress.visible = show_progress
            page.update()

        def do_create_server(e):
            s_name = server_name_input.value.strip() if server_name_input.value else ""
            server_name_input.error_text = None
            if not s_name:
                server_name_input.error_text = "名称不能为空"
                page.update()
                return
            if any(c in s_name for c in r'<>:"/\|?*'):
                server_name_input.error_text = "名称包含无效字符"
                page.update()
                return
            path = os.path.join(SERVERS_ROOT_DIR, s_name)
            current_server_path.current = path
            if not os.path.exists(path):
                try:
                    os.makedirs(path)
                except Exception as ex:
                    update_status(f"创建文件夹失败: {ex}", ft.Colors.RED)
                    return
            update_status(f"已创建服务器 '{s_name}'，请选择核心类型。", ft.Colors.GREEN)
            page.update()

        def show_version_list(core_type):
            version_list_view.controls.clear()
            build_list_view.controls.clear()
            download_button.disabled = True
            update_status(f"正在获取 {core_type} 版本...", ft.Colors.BLUE)
            page.update()
            def add_version_tile(version):
                version_list_view.controls.append(
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.LIST_ROUNDED),
                        title=ft.Text(version),
                        on_click=lambda e, v=version: show_build_list(core_type, v)
                    )
                )
            try:
                if core_type == "Paper":
                    r = requests.get(f"{get_api_base_url('paper')}/v3/projects/paper", timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                    r.raise_for_status()
                    data = r.json()
                    for v in reversed(data['versions']):
                        add_version_tile(v)
                elif core_type == "Purpur":
                    r = requests.get(f"{get_api_base_url('purpur')}/v2/purpur", timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                    r.raise_for_status()
                    data = r.json()
                    for v in reversed(data['versions']):
                        add_version_tile(v)
                elif core_type == "Spigot":
                    r = requests.get(f"{get_api_base_url('getbukkit_page')}/download/spigot", timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                    r.raise_for_status()
                    versions = re.findall(r'<h2><a href=".+?">Spigot ([0-9\.]+)</a></h2>', r.text)
                    if not versions:
                        versions = re.findall(r'href=".+?/spigot-([0-9\.]+)\.jar"', r.text)
                    unique_versions = sorted(list(set(versions)), key=lambda v: list(map(int, v.split('.'))), reverse=True)
                    for v in unique_versions:
                        add_version_tile(v)
                elif core_type == "Vanilla":
                    r = requests.get(f"{get_api_base_url('mojang_meta')}/mc/game/version_manifest.json", timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                    r.raise_for_status()
                    data = r.json()
                    for v in data['versions']:
                        if v['type'] == 'release':
                            add_version_tile(v['id'])
                update_status(f"请选择 {core_type} 版本。", ft.Colors.GREEN)
            except Exception as e:
                update_status(f"获取版本失败: {type(e).__name__}: {e}", ft.Colors.RED)
            page.update()

        def show_build_list(core_type, version):
            build_list_view.controls.clear()
            download_button.disabled = True
            page.update()
            def add_build_tile(build):
                build_list_view.controls.append(
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.DOWNLOAD_ROUNDED),
                        title=ft.Text(str(build)),
                        on_click=lambda e, b=build: enable_download(core_type, version, b)
                    )
                )
            try:
                if core_type == "Paper":
                    r = requests.get(f"{get_api_base_url('paper')}/v3/projects/paper/versions/{version}", timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                    r.raise_for_status()
                    data = r.json()
                    for b in reversed(data['builds']):
                        add_build_tile(b)
                    update_status(f"请选择构建号。", ft.Colors.GREEN)
                elif core_type == "Purpur":
                    r = requests.get(f"{get_api_base_url('purpur')}/v2/purpur/{version}", timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                    r.raise_for_status()
                    data = r.json()
                    for b in reversed(data['builds']['all']):
                        add_build_tile(b)
                    update_status(f"请选择构建号。", ft.Colors.GREEN)
                else:
                    build_list_view.controls.append(ft.Text("无需选择构建号，直接下载。", color=ft.Colors.GREY))
                    enable_download(core_type, version, None)
                    update_status(f"可直接下载 {core_type} {version}", ft.Colors.GREEN)
            except Exception as e:
                update_status(f"获取构建失败: {type(e).__name__}: {e}", ft.Colors.RED)
            page.update()

        def enable_download(core_type, version, build):
            download_button.disabled = False
            download_button.data = (core_type, version, build)
            update_status(f"准备下载 {core_type} {version} {build if build else ''}", ft.Colors.BLUE)
            page.update()

        def download_core_thread(core_type, version, build):
            target_dir = current_server_path.current
            if not target_dir:
                update_status("错误: 目标目录未设置。", ft.Colors.RED)
                return
            try:
                if core_type == "Paper":
                    jar_name = f"paper-{version}-{build}.jar"
                    url = f"{get_api_base_url('paper')}/v3/projects/paper/versions/{version}/builds/{build}/downloads/{jar_name}"
                elif core_type == "Purpur":
                    jar_name = f"purpur-{version}-{build}.jar"
                    url = f"{get_api_base_url('purpur')}/v2/purpur/{version}/{build}/download"
                elif core_type == "Spigot":
                    jar_name = f"spigot-{version}.jar"
                    url = f"{get_api_base_url('getbukkit_cdn')}/spigot/{version}/{jar_name}"
                elif core_type == "Vanilla":
                    r = requests.get(core_api_data['vanilla_versions'][version], timeout=REQUESTS_TIMEOUT, headers=REQUESTS_HEADERS)
                    r.raise_for_status()
                    url = r.json()['downloads']['server']['url']
                    jar_name = "server.jar"
                else:
                    update_status("未知核心类型。", ft.Colors.RED)
                    return
                final_path = os.path.join(target_dir, jar_name)
                download_progress.value = 0
                update_status(f"开始下载 {jar_name}...", ft.Colors.BLUE, True)
                with requests.get(url, stream=True, timeout=300, headers=REQUESTS_HEADERS) as r:
                    r.raise_for_status()
                    total_size = int(r.headers.get('content-length', 0))
                    bytes_downloaded = 0
                    with open(final_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                            bytes_downloaded += len(chunk)
                            if total_size > 0:
                                progress = bytes_downloaded / total_size
                                download_progress.value = progress
                                update_status(f"下载中... {bytes_downloaded // 1024} KB / {total_size // 1024} KB", ft.Colors.BLUE, True)
                try:
                    eula_path = os.path.join(target_dir, "eula.txt")
                    with open(eula_path, 'w') as f:
                        f.write("eula=true\n")
                    update_status(f"下载完成! 已保存到: {final_path} 并自动同意 EULA。", ft.Colors.GREEN)
                except Exception as eula_e:
                    update_status(f"下载完成，但自动同意 EULA 失败: {eula_e}", ft.Colors.ORANGE)
                completion_sound.play()
            except Exception as e:
                update_status(f"下载失败: {type(e).__name__}: {e}", ft.Colors.RED)
            finally:
                download_button.disabled = False
                page.update()

        def start_download(e):
            download_button.disabled = True
            if not download_button.data:
                update_status("未选择下载项。", ft.Colors.RED)
                return
            core_type, version, build = download_button.data
            page.run_thread(download_core_thread, core_type, version, build)

        core_types = ["Paper", "Purpur", "Spigot", "Vanilla"]
        core_type_list = ft.ListView(expand=False, spacing=5)
        for core in core_types:
            core_type_list.controls.append(
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.DOWNLOAD_ROUNDED),
                    title=ft.Text(core, weight=ft.FontWeight.BOLD),
                    on_click=lambda e, c=core: show_version_list(c)
                )
            )

        create_server_button.on_click = do_create_server
        download_button.on_click = start_download

        view = ft.Column([
            ft.Text("服务器核心下载", style=ft.TextThemeStyle.HEADLINE_SMALL),
            SettingsCard("1. 创建新服务器", [ft.Row([server_name_input, create_server_button], spacing=10), ft.Text("服务器文件将保存在 'servers/您输入的名字/' 文件夹中。")]),
            SettingsCard("2. 选择核心类型", [core_type_list]),
            SettingsCard("3. 选择版本", [version_list_view]),
            SettingsCard("4. 选择构建号（如有）", [build_list_view]),
            SettingsCard("5. 下载", [download_button, download_status_text, download_progress]),
        ], expand=True, spacing=10)
        return view

    # --- Page Navigation ---
    def init_navigation():
        current_view = ft.Ref[ft.Control]()
        rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            extended=True,
            min_width=100,
            min_extended_width=150,
            leading=ft.Text("MC Server Panel", style=ft.TextThemeStyle.TITLE_MEDIUM),
            group_alignment=-0.9,
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.HOME_ROUNDED,
                    selected_icon=ft.Icons.HOME_FILLED,
                    label="主页控制台",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.DOWNLOAD_ROUNDED,
                    selected_icon=ft.Icons.DOWNLOAD,
                    label="下载核心",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.GROUP_ROUNDED,
                    selected_icon=ft.Icons.GROUP,
                    label="玩家管理",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.EXTENSION_ROUNDED,
                    selected_icon=ft.Icons.EXTENSION,
                    label="插件管理",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.FOLDER_ROUNDED,
                    selected_icon=ft.Icons.FOLDER,
                    label="文件管理",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_ROUNDED,
                    selected_icon=ft.Icons.SETTINGS,
                    label="设置",
                ),
            ],
            on_change=lambda e: switch_view(e.control.selected_index)
        )

        views = [
            create_home_view(),
            create_core_download_view(),
            create_player_management_view(),
            create_plugin_manager_view(),
            create_file_manager_view(),
            create_settings_view()
        ]
        current_view.current = views[0]

        def switch_view(index):
            content.content = views[index]
            current_view.current = views[index]
            # If the view has a refresh method stored in its data property, call it
            if hasattr(current_view.current, 'data') and callable(current_view.current.data):
                current_view.current.data()
            page.update()

        content = ft.Container(content=current_view.current, expand=True, padding=20)
        page.add(
            ft.Row(
                [
                    rail,
                    ft.VerticalDivider(width=1),
                    content,
                ],
                expand=True,
            )
        )

    init_navigation()

if __name__ == "__main__":
    ft.app(target=main)
