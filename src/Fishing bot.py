import json
import os
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import customtkinter
import win32api
import win32con
from multiprocessing import Queue, freeze_support

from catches_data import (
    catch_display_name,
    ids_for_en_keys,
    load_catches,
)
from i18n import I18n
from memory_bot import MemoryBot

DEFAULT_PREFERENCES = {
    "Catch List": [],
    "auto drink": False,
    "Color Theme": "blue",
    "quick_buff_key": "b",
    "language": "ru",
    "window_geometry": "1100x680",
    "cast_aim": None,
    "projectile_probe": False,
}

VK_MAP = {**{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"}}


def load_preferences(path):
    with open(path, encoding="utf-8") as f:
        prefs = json.load(f)
    for key, value in DEFAULT_PREFERENCES.items():
        prefs.setdefault(key, value)
    prefs.pop("grayscale", None)
    prefs.pop("confidence", None)
    prefs.pop("ui_scaling", None)
    prefs.pop("catch_all", None)
    prefs.pop("remember list", None)
    prefs["quick_buff_key"] = normalize_quick_buff_key(prefs["quick_buff_key"])
    prefs["cast_aim"] = normalize_cast_aim(prefs.get("cast_aim"))
    if prefs.get("language") not in ("en", "ru"):
        prefs["language"] = "en"
    return prefs


def save_preferences():
    with open(preferences_json, "w", encoding="utf-8") as f:
        json.dump(preferences, f, indent=4, ensure_ascii=False)


def normalize_cast_aim(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        x, y = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
    if x < 0 or y < 0 or x > 20000 or y > 20000:
        return None
    return [x, y]


def normalize_quick_buff_key(key):
    key = str(key).strip().lower()
    if len(key) == 1 and key.isalnum():
        return key
    return "b"


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def probe_log_path():
    return get_app_dir() / "projectile_probe.jsonl"


def resource_path(relative):
    rel = Path(relative)
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", get_app_dir()))
        bundled = meipass / rel
        if bundled.exists():
            return bundled
        local = get_app_dir() / rel
        if local.exists():
            return local
    return get_app_dir() / rel


def press_key(key: str):
    vk = VK_MAP.get(key.lower())
    if vk is None:
        return
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


class App(customtkinter.CTk):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue
        self.memory_bot = None
        self._search_after_id = None
        self._catch_visible = {}
        self._stat_name_labels = []
        self._stat_count_labels = []
        self._stat_wraplength = 220
        self._stat_tab_names = [
            i18n.t("tab_fish"),
            i18n.t("tab_quest_fish"),
            i18n.t("tab_usable"),
            i18n.t("tab_crates"),
        ]

        customtkinter.set_appearance_mode("dark")
        customtkinter.set_default_color_theme(preferences["Color Theme"])
        self.title(i18n.t("app_title"))
        icon_path = resource_path("icon.ico")
        if icon_path.exists():
            self.iconbitmap(str(icon_path))

        geometry = preferences.get("window_geometry") or "1100x680"
        self.geometry(geometry)
        self.minsize(720, 480)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._main_tab_names = [
            i18n.t("tab_catch"),
            i18n.t("statistics"),
            i18n.t("tab_options"),
        ]

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        self.left_frame = customtkinter.CTkFrame(self, width=280, corner_radius=0)
        self.left_frame.grid(row=0, column=0, sticky="nsew")
        self.left_frame.grid_columnconfigure(0, weight=1)
        self.left_frame.grid_rowconfigure(5, weight=1)

        self.title_label = customtkinter.CTkLabel(
            self.left_frame,
            text=i18n.t("app_title"),
            font=customtkinter.CTkFont(size=20, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="ew")

        self.start_button = customtkinter.CTkButton(
            self.left_frame,
            text=i18n.t("start"),
            command=self.start_button_event,
            font=customtkinter.CTkFont(size=15),
        )
        self.start_button.grid(row=1, column=0, padx=16, pady=6, sticky="ew")

        self.stop_button = customtkinter.CTkButton(
            self.left_frame,
            text=i18n.t("stop"),
            command=self.stop_button_event,
            font=customtkinter.CTkFont(size=15),
        )
        self.stop_button.grid(row=2, column=0, padx=16, pady=6, sticky="ew")

        self.aim_button = customtkinter.CTkButton(
            self.left_frame,
            text=i18n.t("aim_button"),
            command=self.aim_button_event,
            font=customtkinter.CTkFont(size=14),
        )
        self.aim_button.grid(row=3, column=0, padx=16, pady=6, sticky="ew")

        self.status_label = customtkinter.CTkLabel(
            self.left_frame, text=i18n.t("status_idle"), anchor="w"
        )
        self.status_label.grid(row=4, column=0, padx=16, pady=(8, 4), sticky="ew")

        self.log_textbox = customtkinter.CTkTextbox(self.left_frame)
        self.log_textbox.grid(row=5, column=0, padx=12, pady=(4, 16), sticky="nsew")
        self._bind_log_readonly()

        self.main_tabs = customtkinter.CTkTabview(self)
        self.main_tabs.grid(row=0, column=1, padx=(0, 16), pady=16, sticky="nsew")
        for name in self._main_tab_names:
            self.main_tabs.add(name)

        catch_tab = self.main_tabs.tab(self._main_tab_names[0])
        catch_tab.grid_columnconfigure(0, weight=1)
        catch_tab.grid_rowconfigure(3, weight=1)

        self.catch_list_label = customtkinter.CTkLabel(
            catch_tab,
            text=i18n.t("catch_list"),
            font=customtkinter.CTkFont(size=18, weight="bold"),
        )
        self.catch_list_label.grid(row=0, column=0, padx=10, pady=(6, 0), sticky="w")

        self.catch_search = customtkinter.CTkEntry(
            catch_tab, placeholder_text=i18n.t("catch_search")
        )
        self.catch_search.grid(row=1, column=0, padx=10, pady=(8, 0), sticky="ew")
        self.catch_search.bind("<KeyRelease>", self._on_catch_search)

        self.preset_frame = customtkinter.CTkFrame(catch_tab, fg_color="transparent")
        self.preset_frame.grid(row=2, column=0, padx=10, pady=(8, 0), sticky="ew")
        self.preset_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.preset_fish_button = customtkinter.CTkButton(
            self.preset_frame, text=i18n.t("preset_fish"),
            command=lambda: self._select_preset("Fish"),
        )
        self.preset_fish_button.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.preset_quest_button = customtkinter.CTkButton(
            self.preset_frame, text=i18n.t("preset_quest"),
            command=lambda: self._select_preset("Quest Fish"),
        )
        self.preset_quest_button.grid(row=0, column=1, padx=4, sticky="ew")
        self.preset_crates_button = customtkinter.CTkButton(
            self.preset_frame, text=i18n.t("preset_crates"),
            command=lambda: self._select_preset("Crates"),
        )
        self.preset_crates_button.grid(row=0, column=2, padx=(4, 0), sticky="ew")

        self.catch_checklist = customtkinter.CTkScrollableFrame(
            catch_tab, fg_color=("gray90", "gray17")
        )
        self.catch_checklist.grid(row=3, column=0, padx=10, pady=(8, 0), sticky="nsew")
        self.catch_checklist.grid_columnconfigure(0, weight=1)

        self._catch_vars = {}
        self._catch_checks = {}
        self._build_catch_checkboxes()

        self.actions_frame = customtkinter.CTkFrame(catch_tab, fg_color="transparent")
        self.actions_frame.grid(row=4, column=0, padx=10, pady=(10, 10), sticky="ew")
        self.actions_frame.grid_columnconfigure((0, 1), weight=1)
        self.clear_button = customtkinter.CTkButton(
            self.actions_frame, text=i18n.t("clear"), command=self.clear_button_event
        )
        self.clear_button.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.select_all_button = customtkinter.CTkButton(
            self.actions_frame,
            text=i18n.t("select_all"),
            command=self.select_all_button_event,
        )
        self.select_all_button.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        stats_tab = self.main_tabs.tab(self._main_tab_names[1])
        stats_tab.grid_columnconfigure(0, weight=1)
        stats_tab.grid_rowconfigure(1, weight=1)

        self.statistics_label_title = customtkinter.CTkLabel(
            stats_tab,
            text=i18n.t("statistics"),
            font=customtkinter.CTkFont(size=18, weight="bold"),
        )
        self.statistics_label_title.grid(row=0, column=0, padx=10, pady=(6, 0), sticky="w")

        self.statistics_view = customtkinter.CTkTabview(stats_tab)
        self.statistics_view.configure(fg_color="#333333")
        self.statistics_view.grid(row=1, column=0, padx=10, pady=(8, 12), sticky="nsew")

        self._stat_scrolls = []
        self._stat_header_labels = []
        for tab_name in self._stat_tab_names:
            self.statistics_view.add(tab_name)
            tab = self.statistics_view.tab(tab_name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(1, weight=1)
            header = customtkinter.CTkLabel(
                tab, text=i18n.t("num_catches"), font=("TkDefaultFont", 15)
            )
            header.grid(row=0, column=0, padx=10, pady=(5, 0), sticky="w")
            self._stat_header_labels.append(header)
            scroll = customtkinter.CTkScrollableFrame(tab, fg_color="transparent")
            scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
            scroll.grid_columnconfigure(0, weight=1)
            scroll.grid_columnconfigure(1, weight=0)
            self._stat_scrolls.append(scroll)

        settings_tab = self.main_tabs.tab(self._main_tab_names[2])
        settings_tab.grid_columnconfigure(0, weight=1)

        self.language_label = customtkinter.CTkLabel(
            settings_tab, text=i18n.t("language"), anchor="w"
        )
        self.language_label.grid(row=0, column=0, padx=12, pady=(16, 0), sticky="ew")
        self.language_optionemenu = customtkinter.CTkOptionMenu(
            settings_tab,
            values=[i18n.t("language_en"), i18n.t("language_ru")],
            command=self.change_language_event,
        )
        self.language_optionemenu.grid(row=1, column=0, padx=12, pady=(8, 8), sticky="w")

        self.color_theme_label = customtkinter.CTkLabel(
            settings_tab, text=i18n.t("color_theme"), anchor="w"
        )
        self.color_theme_label.grid(row=2, column=0, padx=12, pady=(10, 0), sticky="ew")
        self.color_theme_optionemenu = customtkinter.CTkOptionMenu(
            settings_tab,
            values=["Blue", "Dark-blue", "Green"],
            command=self.change_color_theme_event,
        )
        self.color_theme_optionemenu.grid(row=3, column=0, padx=12, pady=(8, 8), sticky="w")

        self.auto_drink_switch = customtkinter.CTkSwitch(
            settings_tab,
            text=i18n.t("auto_drink"),
            command=self.save_switch_preferences,
        )
        self.auto_drink_switch.grid(row=4, column=0, padx=12, pady=(16, 0), sticky="w")

        self.projectile_probe_switch = customtkinter.CTkSwitch(
            settings_tab,
            text=i18n.t("projectile_probe"),
            command=self.save_switch_preferences,
        )
        self.projectile_probe_switch.grid(row=5, column=0, padx=12, pady=(12, 0), sticky="w")

        self.quick_buff_label = customtkinter.CTkLabel(
            settings_tab, text=i18n.t("quick_buff_key"), anchor="w"
        )
        self.quick_buff_label.grid(row=6, column=0, padx=12, pady=(16, 0), sticky="w")
        self.quick_buff_entry = customtkinter.CTkEntry(settings_tab, width=60)
        self.quick_buff_entry.grid(row=7, column=0, padx=12, pady=(8, 16), sticky="w")
        self.quick_buff_entry.insert(0, preferences["quick_buff_key"])
        self.quick_buff_entry.bind("<FocusOut>", self.quick_buff_key_event)
        self.quick_buff_entry.bind("<Return>", self.quick_buff_key_event)

        if preferences["auto drink"]:
            self.auto_drink_switch.select()
        else:
            self.auto_drink_switch.deselect()
        if preferences.get("projectile_probe"):
            self.projectile_probe_switch.select()
        else:
            self.projectile_probe_switch.deselect()

        self.stop_button.configure(state="disabled")
        self.color_theme_optionemenu.set(preferences["Color Theme"].capitalize())
        self._set_language_menu_value(preferences["language"])
        self.log_textbox.insert("0.0", f"{i18n.t('fishing_log')}\n\n")

        self.set_selected_en_keys(preferences.get("Catch List", []))
        self.build_statistics_rows()

    def _build_catch_checkboxes(self):
        for child in self.catch_checklist.winfo_children():
            child.destroy()
        self._catch_vars = {}
        self._catch_checks = {}
        self._catch_visible = {}
        selected = set(preferences.get("Catch List", []))
        for i, en_key in enumerate(CATCH_NAMES):
            var = tk.BooleanVar(value=en_key in selected)
            check = customtkinter.CTkCheckBox(
                self.catch_checklist,
                text=self.display_name_for_en(en_key),
                variable=var,
                command=lambda k=en_key: self._on_catch_toggled(k),
                checkbox_width=18,
                checkbox_height=18,
                font=customtkinter.CTkFont(size=13),
            )
            check.grid(row=i, column=0, sticky="ew", padx=4, pady=1)
            self._catch_vars[en_key] = var
            self._catch_checks[en_key] = check
            self._catch_visible[en_key] = True
            self._style_catch_row(en_key)

    def _on_catch_search(self, _event=None):
        if self._search_after_id is not None:
            try:
                self.after_cancel(self._search_after_id)
            except Exception:
                pass
        self._search_after_id = self.after(150, self._apply_catch_filter)

    def _apply_catch_filter(self):
        self._search_after_id = None
        query = ""
        try:
            query = self.catch_search.get().strip().lower()
        except Exception:
            pass
        for i, en_key in enumerate(CATCH_NAMES):
            check = self._catch_checks.get(en_key)
            if not check:
                continue
            name = self.display_name_for_en(en_key).lower()
            visible = (not query) or query in name or query in en_key.lower()
            if self._catch_visible.get(en_key) == visible:
                continue
            self._catch_visible[en_key] = visible
            if visible:
                check.grid(row=i, column=0, sticky="ew", padx=4, pady=1)
            else:
                check.grid_remove()

    def _select_preset(self, category):
        keys = list(statistics_data.get(category, {}).keys())
        self.set_selected_en_keys(keys)
        preferences["Catch List"] = self.get_selected_en_keys()
        save_preferences()

    def _style_catch_row(self, en_key):
        check = self._catch_checks.get(en_key)
        var = self._catch_vars.get(en_key)
        if not check or not var:
            return
        if var.get():
            check.configure(text_color="#3dd68c")
        else:
            check.configure(text_color=("gray10", "gray90"))

    def _on_catch_toggled(self, en_key):
        self._style_catch_row(en_key)
        preferences["Catch List"] = self.get_selected_en_keys()
        save_preferences()

    def set_catch_controls_state(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.clear_button.configure(state=state)
        self.select_all_button.configure(state=state)
        self.preset_fish_button.configure(state=state)
        self.preset_quest_button.configure(state=state)
        self.preset_crates_button.configure(state=state)
        self.catch_search.configure(state=state)
        for check in self._catch_checks.values():
            check.configure(state=state)

    def build_statistics_rows(self):
        for scroll in self._stat_scrolls:
            for child in scroll.winfo_children():
                child.destroy()
        self._stat_name_labels = []
        self._stat_count_labels = []

        categories = (
            statistics_data["Fish"],
            statistics_data["Quest Fish"],
            statistics_data["Usable Items"],
            statistics_data["Crates"],
        )
        for cat_index, mapping in enumerate(categories):
            scroll = self._stat_scrolls[cat_index]
            names = []
            counts = []
            for i, (catch, value) in enumerate(mapping.items()):
                name = self.stat_display_name(catch)
                name_label = customtkinter.CTkLabel(
                    scroll,
                    text=name,
                    font=customtkinter.CTkFont(size=13),
                    anchor="w",
                    justify="left",
                    wraplength=self._stat_wraplength,
                )
                name_label.grid(row=i, column=0, sticky="ew", padx=(4, 8), pady=2)
                count_label = customtkinter.CTkLabel(
                    scroll,
                    text=str(value),
                    font=customtkinter.CTkFont(size=13),
                    anchor="e",
                )
                count_label.grid(row=i, column=1, sticky="e", padx=(0, 4), pady=2)
                names.append(name_label)
                counts.append(count_label)
            self._stat_name_labels.append(names)
            self._stat_count_labels.append(counts)

    def current_lang(self):
        return preferences.get("language", "ru")

    def stat_display_name(self, en_key):
        entry = CATCHES_BY_EN.get(en_key)
        if entry:
            return catch_display_name(entry, self.current_lang())
        return en_key

    def display_name_for_en(self, en_key):
        return self.stat_display_name(en_key)

    def refresh_catch_checkbox_labels(self):
        for en_key, check in self._catch_checks.items():
            check.configure(text=self.display_name_for_en(en_key))
            self._style_catch_row(en_key)

    def set_selected_en_keys(self, en_keys):
        selected = set(en_keys)
        for en_key, var in self._catch_vars.items():
            var.set(en_key in selected)
            self._style_catch_row(en_key)

    def get_selected_en_keys(self):
        return [en_key for en_key, var in self._catch_vars.items() if var.get()]

    def _set_language_menu_value(self, lang):
        if lang == "ru":
            self.language_optionemenu.set(i18n.t("language_ru"))
        else:
            self.language_optionemenu.set(i18n.t("language_en"))

    def _language_from_menu(self, label):
        if label == i18n.t("language_ru"):
            return "ru"
        return "en"

    def change_language_event(self, label):
        lang = self._language_from_menu(label)
        if lang == preferences.get("language"):
            return
        preferences["language"] = lang
        save_preferences()
        self.apply_language()

    def _rename_tabview_tabs(self, tabview, old_names, new_names):
        # Use the public CTkTabview.rename() API (customtkinter >= 5.x) which
        # synchronizes _segmented_button, _name_list and _tab_dict in one shot.
        # The old manual mutation of private dicts left the segmented button out
        # of sync and produced ghost/doubled text on rapid language switches.
        if list(old_names) == list(new_names):
            return
        current_name = tabview.get()
        for old, new in zip(old_names, new_names):
            if old == new:
                continue
            try:
                tabview.rename(old, new)
            except (ValueError, KeyError):
                pass
        if current_name in old_names:
            idx = old_names.index(current_name)
            if idx < len(new_names):
                tabview.set(new_names[idx])

    def apply_language(self):
        i18n.load(preferences["language"])
        self.title(i18n.t("app_title"))
        self.title_label.configure(text=i18n.t("app_title"))
        self.start_button.configure(text=i18n.t("start"))
        self.stop_button.configure(text=i18n.t("stop"))
        self.aim_button.configure(text=i18n.t("aim_button"))
        if getattr(self, "memory_bot", None) is None:
            self.status_label.configure(text=i18n.t("status_idle"))
        self.language_label.configure(text=i18n.t("language"))
        self._set_language_menu_value(preferences["language"])
        self.language_optionemenu.configure(
            values=[i18n.t("language_en"), i18n.t("language_ru")]
        )
        self.color_theme_label.configure(text=i18n.t("color_theme"))

        new_main = [
            i18n.t("tab_catch"),
            i18n.t("statistics"),
            i18n.t("tab_options"),
        ]
        self._rename_tabview_tabs(self.main_tabs, self._main_tab_names, new_main)
        self._main_tab_names = new_main

        self.catch_list_label.configure(text=i18n.t("catch_list"))
        self.catch_search.configure(placeholder_text=i18n.t("catch_search"))
        self.preset_fish_button.configure(text=i18n.t("preset_fish"))
        self.preset_quest_button.configure(text=i18n.t("preset_quest"))
        self.preset_crates_button.configure(text=i18n.t("preset_crates"))
        self.clear_button.configure(text=i18n.t("clear"))
        self.select_all_button.configure(text=i18n.t("select_all"))
        self.auto_drink_switch.configure(text=i18n.t("auto_drink"))
        self.projectile_probe_switch.configure(text=i18n.t("projectile_probe"))
        self.quick_buff_label.configure(text=i18n.t("quick_buff_key"))

        self.statistics_label_title.configure(text=i18n.t("statistics"))
        new_stat_names = [
            i18n.t("tab_fish"),
            i18n.t("tab_quest_fish"),
            i18n.t("tab_usable"),
            i18n.t("tab_crates"),
        ]
        self._rename_tabview_tabs(
            self.statistics_view, self._stat_tab_names, new_stat_names
        )
        self._stat_tab_names = new_stat_names
        for header in self._stat_header_labels:
            header.configure(text=i18n.t("num_catches"))

        en_keys = self.get_selected_en_keys()
        preferences["Catch List"] = en_keys
        self.refresh_catch_checkbox_labels()
        self.update_statistics()
        self._refresh_log_menu_labels()

    def update_statistics(self):
        categories = (
            statistics_data["Fish"],
            statistics_data["Quest Fish"],
            statistics_data["Usable Items"],
            statistics_data["Crates"],
        )
        for cat_index, mapping in enumerate(categories):
            for i, (catch, value) in enumerate(mapping.items()):
                self._stat_name_labels[cat_index][i].configure(
                    text=self.stat_display_name(catch),
                )
                self._stat_count_labels[cat_index][i].configure(text=str(value))

    def _bind_log_readonly(self):
        inner = getattr(self.log_textbox, "_textbox", None)
        self._log_menu = tk.Menu(self, tearoff=0)
        self._log_menu.add_command(
            label=i18n.t("log_copy"), command=self._copy_log_selection
        )
        self._log_menu.add_command(
            label=i18n.t("log_select_all"), command=self._select_all_log
        )
        for widget in (self.log_textbox, inner):
            if widget is None:
                continue
            widget.bind("<Key>", self._on_log_key)
            widget.bind("<Button-3>", self._on_log_right_click)
            widget.bind("<Control-c>", self._copy_log_selection)
            widget.bind("<Control-C>", self._copy_log_selection)
            widget.bind("<Control-a>", self._select_all_log)
            widget.bind("<Control-A>", self._select_all_log)
            widget.bind("<Control-Insert>", self._copy_log_selection)

    def _refresh_log_menu_labels(self):
        menu = getattr(self, "_log_menu", None)
        if menu is None:
            return
        try:
            menu.entryconfig(0, label=i18n.t("log_copy"))
            menu.entryconfig(1, label=i18n.t("log_select_all"))
        except tk.TclError:
            pass

    def _log_text_widget(self):
        return getattr(self.log_textbox, "_textbox", None) or self.log_textbox

    def _copy_log_selection(self, _event=None):
        widget = self._log_text_widget()
        try:
            text = widget.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        self.clipboard_clear()
        self.clipboard_append(text)
        try:
            self.update_idletasks()
        except tk.TclError:
            pass
        return "break"

    def _select_all_log(self, _event=None):
        widget = self._log_text_widget()
        try:
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "1.0")
            widget.see("insert")
        except tk.TclError:
            pass
        return "break"

    def _on_log_right_click(self, event):
        menu = getattr(self, "_log_menu", None)
        if menu is None:
            return "break"
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _on_log_key(self, event):
        if event.keysym in (
            "Left", "Right", "Up", "Down", "Home", "End",
            "Prior", "Next", "Shift_L", "Shift_R", "Control_L", "Control_R",
            "Alt_L", "Alt_R",
        ):
            return None
        ctrl = bool(event.state & 0x4)
        if ctrl and event.keycode in (67, 45):
            return self._copy_log_selection(event)
        if ctrl and event.keycode == 65:
            return self._select_all_log(event)
        if ctrl and event.keysym.lower() in ("c", "insert", "cyrillic_es"):
            return self._copy_log_selection(event)
        if ctrl and event.keysym.lower() in ("a", "cyrillic_ef"):
            return self._select_all_log(event)
        return "break"

    def log_message(self, message):
        self.log_textbox.insert("end", message)
        self.log_textbox.see("end")

    def change_color_theme_event(self, new_color_theme: str):
        customtkinter.set_default_color_theme(new_color_theme.lower())
        self.log_message(i18n.t("theme_selected", theme=new_color_theme) + "\n")
        self.log_message(i18n.t("theme_restart") + "\n\n")
        preferences["Color Theme"] = new_color_theme.lower()
        save_preferences()

    def quick_buff_key_event(self, _event=None):
        normalized = normalize_quick_buff_key(self.quick_buff_entry.get())
        if normalized != self.quick_buff_entry.get().strip().lower():
            self.log_message(i18n.t("invalid_buff_key", key=normalized) + "\n\n")
        self.quick_buff_entry.delete(0, "end")
        self.quick_buff_entry.insert(0, normalized)
        preferences["quick_buff_key"] = normalized
        save_preferences()

    def start_button_event(self):
        self.quick_buff_key_event()
        en_keys = self.get_selected_en_keys()
        if not en_keys:
            self.log_message(i18n.t("no_catches") + "\n")
            self.log_message(i18n.t("please_select") + "\n")
            return

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.set_catch_controls_state(False)
        bot.stop_event.clear()

        item_ids = ids_for_en_keys(en_keys, CATCHES_BY_EN)

        self.memory_bot = MemoryBot(
            on_catch=self._on_memory_catch,
            on_status=self._on_memory_status,
            on_error=self._on_memory_error,
            whitelist_ids=set(item_ids),
            poll_interval=0.025,
            aim_client=preferences.get("cast_aim"),
            on_aim=self._on_aim_saved,
            probe_enabled=bool(preferences.get("projectile_probe")),
            probe_log_path=str(probe_log_path()),
        )
        self.log_message(i18n.t("mem_looking") + "\n")
        self.log_message(i18n.t("hook_started") + "\n")
        if preferences.get("projectile_probe"):
            self.log_message(
                i18n.t("projectile_probe_file", path=str(probe_log_path())) + "\n"
            )
        self.log_message("\n")
        self.save_switch_preferences()
        self.memory_bot.start()

        if preferences["auto drink"]:
            self.drink_thread = threading.Thread(
                target=bot.auto_drink,
                args=(time.monotonic(), preferences["quick_buff_key"]),
                daemon=True,
            )
            self.drink_thread.start()

    def _on_memory_status(self, msg: str):
        """MemoryBot status callback — marshal onto the Tk thread."""
        def ui():
            if msg == "scanning":
                self.log_message(i18n.t("mem_scanning") + "\n")
            elif msg == "scanning_heap":
                self.log_message(i18n.t("mem_scanning_heap") + "\n")
            elif msg == "scanning_heap_full":
                self.log_message(i18n.t("mem_scanning_heap_full") + "\n")
            elif msg.startswith("progress:"):
                pct = msg.split(":", 1)[1]
                self.log_message(i18n.t("mem_scanning_pct", pct=pct) + "\n")
            elif msg in ("hooked", "hooked_cached"):
                self.status_label.configure(text=i18n.t("mem_hooked"))
                self.log_message(i18n.t("mem_hooked") + "\n")
            elif msg == "aim_prompt":
                self.status_label.configure(text=i18n.t("aim_button"))
                self.log_message(i18n.t("aim_prompt") + "\n")
            elif msg.startswith("aim_set:"):
                self.status_label.configure(text=i18n.t("aim_set"))
                self.log_message(i18n.t("aim_set") + "\n")
            elif msg == "scan_aborted":
                return
            elif msg.startswith("rolled:"):
                self.log_message(msg + "\n")
            elif msg.startswith("phase:"):
                self.log_message(msg + "\n")
            elif msg.startswith("bite:"):
                parts = msg.split(":")
                if len(parts) >= 3:
                    self.log_message(
                        f"bite id={parts[1]} {parts[2]}\n"
                    )
            elif msg.startswith("click:"):
                self.log_message(msg + "\n")
            elif msg == "reel_failed":
                self.log_message(i18n.t("mem_reel_failed") + "\n")
            elif msg == "recast_failed":
                self.log_message(i18n.t("mem_recast_failed") + "\n")
            elif msg == "focus_failed":
                self.log_message(i18n.t("mem_focus_failed") + "\n")
            elif msg.startswith("safe_stop:"):
                reason = msg.split(":", 1)[1]
                self.log_message(i18n.t("mem_safe_stop", reason=reason) + "\n\n")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.set_catch_controls_state(True)
                self.status_label.configure(text=i18n.t("status_idle"))
                bot.stop_event.set()
                self.memory_bot = None
        try:
            self.after(0, ui)
        except Exception:
            pass

    def _on_memory_error(self, err: str):
        """MemoryBot error callback — marshal onto the Tk thread."""
        def ui():
            if err == "terraria_not_running":
                self.log_message(i18n.t("mem_not_running") + "\n\n")
            elif err == "terraria_is_64bit":
                self.log_message(i18n.t("mem_need_x86") + "\n\n")
            elif err == "signature_not_found":
                self.log_message(i18n.t("mem_sig_missing") + "\n\n")
            elif err == "context_null":
                self.log_message(i18n.t("mem_context_null") + "\n\n")
            elif err == "player_not_found":
                self.log_message(i18n.t("mem_player_missing") + "\n\n")
            else:
                self.log_message(i18n.t("mem_error", error=err) + "\n\n")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.set_catch_controls_state(True)
            self.status_label.configure(text=i18n.t("status_idle"))
            bot.stop_event.set()
        try:
            self.after(0, ui)
        except Exception:
            pass

    def _on_memory_catch(self, item_id):
        """Called from MemoryBot's background thread when a fish is reeled in."""
        try:
            self.after(0, lambda: self.queue.put(int(item_id)))
        except Exception:
            self.queue.put(int(item_id))

    def stop_button_event(self):
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.set_catch_controls_state(True)
        bot.stop_event.set()
        if getattr(self, "memory_bot", None) is not None:
            self.memory_bot.stop()
            self.memory_bot = None
        self.log_message(i18n.t("hook_stopped") + "\n\n")
        self.status_label.configure(text=i18n.t("status_idle"))
        self.save_switch_preferences()

    def aim_button_event(self):
        preferences["cast_aim"] = None
        save_preferences()
        running = (
            getattr(self, "memory_bot", None) is not None
            and self.memory_bot.thread is not None
            and self.memory_bot.thread.is_alive()
        )
        if running:
            self.memory_bot.request_aim()
        self.status_label.configure(text=i18n.t("aim_button"))
        self.log_message(i18n.t("aim_prompt") + "\n")

    def _on_aim_saved(self, xy):
        def ui():
            preferences["cast_aim"] = [int(xy[0]), int(xy[1])]
            save_preferences()
        try:
            self.after(0, ui)
        except Exception:
            ui()

    def select_all_button_event(self):
        self.set_selected_en_keys(CATCH_NAMES)
        preferences["Catch List"] = list(CATCH_NAMES)
        save_preferences()
        self.log_message(i18n.t("selected_all", count=len(CATCH_NAMES)) + "\n\n")

    def clear_button_event(self):
        self.set_selected_en_keys([])
        preferences["Catch List"] = []
        save_preferences()

    def process_queue_data(self, queue):
        while True:
            item_id = queue.get()
            entry = CATCHES_BY_ID.get(int(item_id))
            if entry:
                en_key = entry["en"]
                display = catch_display_name(entry, preferences.get("language", "en"))
            else:
                en_key = None
                display = i18n.t("unknown_item", id=item_id)
            app.log_message(i18n.t("caught", name=display) + "\n")
            if not en_key:
                continue
            for key in statistics_data.keys():
                if en_key in statistics_data[key]:
                    statistics_data[key][en_key] += 1
                    self.update_statistics()
                    with open(statistics_json, "w", encoding="utf-8") as f:
                        json.dump(statistics_data, f, indent=4, ensure_ascii=False)
                    break

    def save_switch_preferences(self):
        preferences["auto drink"] = self.auto_drink_switch.get() == 1
        probe_on = self.projectile_probe_switch.get() == 1
        was_probe = bool(preferences.get("projectile_probe"))
        preferences["projectile_probe"] = probe_on
        save_preferences()
        if getattr(self, "memory_bot", None) is not None:
            self.memory_bot.set_probe_enabled(probe_on)
        if probe_on and not was_probe:
            self.log_message(
                i18n.t("projectile_probe_file", path=str(probe_log_path())) + "\n"
            )

    def on_close(self):
        preferences["window_geometry"] = self.geometry()
        preferences["Catch List"] = self.get_selected_en_keys()
        if getattr(self, "memory_bot", None) is not None:
            self.memory_bot.stop()
            self.memory_bot = None
        bot.stop_event.set()
        save_preferences()
        self.destroy()


class FishingBot:
    def __init__(self):
        self.stop_event = threading.Event()

    def auto_drink(self, current_time, quick_buff_key):
        key = normalize_quick_buff_key(quick_buff_key)
        while not self.stop_event.is_set():
            press_key(key)
            sleep_for = 61.0 - ((time.monotonic() - current_time) % 61.0)
            end = time.monotonic() + sleep_for
            while time.monotonic() < end:
                if self.stop_event.is_set():
                    return
                time.sleep(0.2)


if __name__ == "__main__":
    freeze_support()

    app_dir = get_app_dir()
    if getattr(sys, "frozen", False):
        os.chdir(app_dir)

    path = app_dir
    statistics_json = path.joinpath("statistics.json")
    preferences_json = path.joinpath("preferences.json")

    if getattr(sys, "frozen", False):
        bundled_prefs = resource_path("preferences.json")
        bundled_stats = resource_path("statistics.json")
        if not preferences_json.exists() and bundled_prefs.exists():
            shutil.copy(bundled_prefs, preferences_json)
        if not statistics_json.exists() and bundled_stats.exists():
            shutil.copy(bundled_stats, statistics_json)

    with open(statistics_json, encoding="utf-8") as f:
        statistics_data = json.load(f)

    preferences = load_preferences(preferences_json)
    i18n = I18n(resource_path)
    i18n.load(preferences.get("language", "ru"))

    _, CATCH_NAMES, CATCHES_BY_EN, DISPLAY_TO_EN, CATCHES_BY_ID = load_catches(
        resource_path
    )

    queue = Queue()
    app = App(queue)
    bot = FishingBot()

    queue_thread = threading.Thread(target=app.process_queue_data, args=(queue,), daemon=True)
    queue_thread.start()

    app.update_statistics()
    app.mainloop()
